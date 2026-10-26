"""
Xtream Codes API Player router (/player_api.php).
Implements the standard Xtream Codes API specification for IPTV players (TiviMate, Smarters, etc.).
"""
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.channel import Channel
from app.models.movie import Movie
from app.models.series import Episode, Series
from app.services.category_helper import group_to_category_id

router = APIRouter(tags=["Xtream API"])


def check_auth(username: Optional[str], password: Optional[str]) -> bool:
    """Validate output Xtream credentials."""
    return bool(
        username
        and password
        and username == settings.OUTPUT_USERNAME
        and password == settings.OUTPUT_PASSWORD
    )


def auth_failure_response():
    """Return standard Xtream authentication failure JSON."""
    return JSONResponse(
        content={"user_info": {"auth": 0, "status": "Disabled", "message": "Invalid username or password"}}
    )


@router.api_route("/player_api.php", methods=["GET", "POST"])
async def player_api(
    request: Request,
    username: Optional[str] = Query(None),
    password: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    category_id: Optional[str] = Query(None),
    vod_id: Optional[str] = Query(None),
    series_id: Optional[str] = Query(None),
    stream_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """
    Core Xtream Codes API gateway endpoint supporting both GET and POST requests.
    """
    # Also support form-data or query params if sent via POST
    if request.method == "POST":
        try:
            form = await request.form()
            username = username or form.get("username")
            password = password or form.get("password")
            action = action or form.get("action")
            category_id = category_id or form.get("category_id")
            vod_id = vod_id or form.get("vod_id")
            series_id = series_id or form.get("series_id")
            stream_id = stream_id or form.get("stream_id")
        except Exception:
            pass

    if not check_auth(username, password):
        return auth_failure_response()

    # Determine server host and port from request
    req_url = request.url
    server_port = str(req_url.port or (443 if req_url.scheme == "https" else 80))
    server_protocol = req_url.scheme

    # 1. Base auth call (no action specified)
    if not action:
        return {
            "user_info": {
                "username": username,
                "password": password,
                "message": f"Welcome to {settings.SERVER_NAME}",
                "auth": 1,
                "status": "Active",
                "exp_date": "1798761600",  # Long-lived expiry
                "is_trial": "0",
                "active_cons": "0",
                "created_at": "1700000000",
                "max_connections": "5",
                "allowed_output_formats": ["m3u8", "ts", "rtmp"],
            },
            "server_info": {
                "url": req_url.hostname or "127.0.0.1",
                "port": server_port,
                "https_port": server_port if server_protocol == "https" else "",
                "server_protocol": server_protocol,
                "rtmp_port": "",
                "timezone": "UTC",
                "time_now": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
            },
        }

    # 2. Live Categories
    if action == "get_live_categories":
        distinct_groups = (
            db.query(Channel.display_group)
            .filter(Channel.is_hidden == False)
            .distinct()
            .all()
        )
        categories = []
        for (grp,) in distinct_groups:
            group_name = grp or "Uncategorized"
            cid = group_to_category_id(group_name)
            categories.append({
                "category_id": str(cid),
                "category_name": group_name,
                "parent_id": 0,
            })
        return categories

    # 3. Live Streams
    if action == "get_live_streams":
        query = db.query(Channel).filter(Channel.is_hidden == False)
        channels = query.all()

        streams = []
        target_cid = str(category_id) if category_id and category_id != "0" else None

        for ch in channels:
            cid = str(group_to_category_id(ch.display_group))
            if target_cid and cid != target_cid:
                continue

            streams.append({
                "num": ch.id,
                "name": ch.display_name,
                "stream_type": "live",
                "stream_id": ch.id,
                "stream_icon": ch.logo_url or "",
                "epg_channel_id": ch.epg_channel_id or "",
                "added": "1700000000",
                "category_id": cid,
                "custom_sid": "",
                "tv_archive": 0,
                "direct_source": "",
                "tv_archive_duration": 0,
            })
        return streams

    # 4. VOD Categories
    if action == "get_vod_categories":
        distinct_groups = (
            db.query(Movie.display_group)
            .filter(Movie.is_hidden == False)
            .distinct()
            .all()
        )
        categories = []
        for (grp,) in distinct_groups:
            group_name = grp or "Uncategorized"
            cid = group_to_category_id(group_name)
            categories.append({
                "category_id": str(cid),
                "category_name": group_name,
                "parent_id": 0,
            })
        return categories

    # 5. VOD Streams
    if action == "get_vod_streams":
        query = db.query(Movie).filter(Movie.is_hidden == False)
        movies = query.all()

        streams = []
        target_cid = str(category_id) if category_id and category_id != "0" else None

        for mv in movies:
            cid = str(group_to_category_id(mv.display_group))
            if target_cid and cid != target_cid:
                continue

            rating_val = mv.rating or 0.0
            streams.append({
                "num": mv.id,
                "name": mv.display_name,
                "stream_type": "movie",
                "stream_id": mv.id,
                "stream_icon": mv.poster_url or "",
                "rating": rating_val,
                "rating_5based": round(rating_val / 2.0, 1),
                "added": "1700000000",
                "category_id": cid,
                "container_extension": mv.container_ext or "mp4",
            })
        return streams

    # 6. VOD Info
    if action == "get_vod_info":
        target_id = vod_id or stream_id
        if not target_id:
            return {}
        try:
            m_id = int(target_id)
        except ValueError:
            return {}

        movie = db.query(Movie).filter(Movie.id == m_id, Movie.is_hidden == False).first()
        if not movie:
            return {}

        cid = str(group_to_category_id(movie.display_group))
        return {
            "info": {
                "name": movie.display_name,
                "description": movie.plot or "",
                "genre": movie.genre or "",
                "rating": movie.rating or 0.0,
                "releasedate": str(movie.year or ""),
                "movie_image": movie.poster_url or "",
                "backdrop_path": [movie.backdrop_url] if movie.backdrop_url else [],
                "tmdb_id": movie.tmdb_id,
            },
            "movie_data": {
                "stream_id": movie.id,
                "name": movie.display_name,
                "container_extension": movie.container_ext or "mp4",
                "category_id": cid,
            },
        }

    # 7. Series Categories
    if action == "get_series_categories":
        distinct_groups = (
            db.query(Series.display_group)
            .filter(Series.is_hidden == False)
            .distinct()
            .all()
        )
        categories = []
        for (grp,) in distinct_groups:
            group_name = grp or "Uncategorized"
            cid = group_to_category_id(group_name)
            categories.append({
                "category_id": str(cid),
                "category_name": group_name,
                "parent_id": 0,
            })
        return categories

    # 8. Series List
    if action == "get_series":
        query = db.query(Series).filter(Series.is_hidden == False)
        series_all = query.all()

        target_cid = str(category_id) if category_id and category_id != "0" else None
        output = []

        for s in series_all:
            cid = str(group_to_category_id(s.display_group))
            if target_cid and cid != target_cid:
                continue

            output.append({
                "num": s.id,
                "name": s.display_name,
                "series_id": s.id,
                "cover": s.poster_url or "",
                "plot": s.plot or "",
                "genre": s.genre or "",
                "releaseDate": str(s.year or ""),
                "rating": s.rating or 0.0,
                "rating_5based": round((s.rating or 0.0) / 2.0, 1),
                "category_id": cid,
            })
        return output

    # 9. Series Info
    if action == "get_series_info":
        if not series_id:
            return {}
        try:
            s_id = int(series_id)
        except ValueError:
            return {}

        s = db.query(Series).filter(Series.id == s_id, Series.is_hidden == False).first()
        if not s:
            return {}

        episodes = db.query(Episode).filter(Episode.series_id == s.id).all()
        episodes_by_season: Dict[str, List[Dict[str, Any]]] = {}

        for ep in episodes:
            s_num = str(ep.season_num or 1)
            if s_num not in episodes_by_season:
                episodes_by_season[s_num] = []

            episodes_by_season[s_num].append({
                "id": ep.id,
                "episode_num": ep.episode_num or 1,
                "title": ep.title or f"Episode {ep.episode_num or 1}",
                "container_extension": ep.container_ext or "mp4",
                "info": {},
            })

        return {
            "seasons": [{"season_number": int(sn)} for sn in episodes_by_season.keys()] or [{"season_number": 1}],
            "info": {
                "name": s.display_name,
                "cover": s.poster_url or "",
                "plot": s.plot or "",
                "genre": s.genre or "",
                "releaseDate": str(s.year or ""),
                "rating": s.rating or 0.0,
            },
            "episodes": episodes_by_season,
        }

    # 10. Fallback for unhandled actions (e.g. get_short_epg)
    return []
