"""
Stream Proxy router for Pream.
Redirects IPTV clients (TiviMate, VLC, etc.) directly to upstream stream URLs via HTTP 302,
ensuring zero server bandwidth consumption. Includes transparent zero-bandwidth failover
to healthy secondary providers and 30-day playback history logging.
Also provides web preview streaming relays and 1-click desktop player (.m3u) launchers.
"""
from typing import Optional, Tuple

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.channel import Channel
from app.models.movie import Movie
from app.models.series import Episode, Series
from app.models.source import Source
from app.services.auth import require_admin
from app.services.ingestion.xtream import clean_base_url
from app.services.stream_logs import (
    find_failover_channel,
    find_failover_movie,
    record_stream_log,
)

router = APIRouter(tags=["Stream Proxy"])


def verify_stream_auth(username: str, password: str):
    """Verify stream client credentials."""
    if username != settings.OUTPUT_USERNAME or password != settings.OUTPUT_PASSWORD:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid stream credentials",
        )


def extract_client_info(request: Request) -> Tuple[str, str]:
    """Safely extract client IP and user agent."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        client_ip = forwarded.split(",")[0].strip()
    elif request.client and request.client.host:
        client_ip = request.client.host
    else:
        client_ip = "unknown"
    user_agent = request.headers.get("user-agent", "")
    return client_ip, user_agent


@router.get("/live/{username}/{password}/{stream_spec}")
def proxy_live_stream(
    username: str,
    password: str,
    stream_spec: str,  # e.g. "123.ts" or "123.m3u8"
    request: Request,
    db: Session = Depends(get_db),
):
    """Redirect live stream request to upstream provider stream URL (with zero-bandwidth failover)."""
    verify_stream_auth(username, password)

    # Extract ID from "123.ts" or "123.m3u8" or "123"
    stream_id_str = stream_spec.split(".")[0]
    try:
        channel_id = int(stream_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid stream ID format")

    channel = (
        db.query(Channel)
        .filter(Channel.id == channel_id, Channel.is_hidden == False)
        .first()
    )
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found or hidden")

    # Check for transparent failover if primary source is down
    target_channel = channel
    is_failover = False
    failover = find_failover_channel(db, channel)
    if failover:
        target_channel, _ = failover
        is_failover = True

    stream_url = target_channel.stream_url
    if not stream_url:
        # Reconstruct stream URL from source if not stored
        source = db.query(Source).filter(Source.id == target_channel.source_id).first()
        if source and source.type == "xtream":
            b_url = clean_base_url(source.url)
            stream_url = f"{b_url}/live/{source.username}/{source.password}/{target_channel.source_stream_id}.ts"

    if not stream_url:
        raise HTTPException(status_code=502, detail="Upstream stream URL not available")

    # Record stream history (IP, title, timestamp)
    client_ip, user_agent = extract_client_info(request)
    record_stream_log(
        db=db,
        stream_type="live",
        stream_id=channel.id,
        stream_title=channel.display_name or channel.raw_name,
        client_ip=client_ip,
        user_agent=user_agent,
        source_id=target_channel.source_id,
        is_failover=is_failover,
    )

    # Issue HTTP 302 redirect directly to upstream provider
    return RedirectResponse(url=stream_url, status_code=status.HTTP_302_FOUND)


@router.get("/movie/{username}/{password}/{stream_spec}")
def proxy_movie_stream(
    username: str,
    password: str,
    stream_spec: str,  # e.g. "123.mp4" or "123.mkv"
    request: Request,
    db: Session = Depends(get_db),
):
    """Redirect movie VOD request to upstream provider stream URL (with zero-bandwidth failover)."""
    verify_stream_auth(username, password)

    stream_id_str = stream_spec.split(".")[0]
    try:
        movie_id = int(stream_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid stream ID format")

    movie = (
        db.query(Movie)
        .filter(Movie.id == movie_id, Movie.is_hidden == False)
        .first()
    )
    if not movie:
        raise HTTPException(status_code=404, detail="Movie not found or hidden")

    target_movie = movie
    is_failover = False
    failover = find_failover_movie(db, movie)
    if failover:
        target_movie, _ = failover
        is_failover = True

    stream_url = target_movie.stream_url
    if not stream_url:
        source = db.query(Source).filter(Source.id == target_movie.source_id).first()
        if source and source.type == "xtream":
            b_url = clean_base_url(source.url)
            ext = target_movie.container_ext or "mp4"
            stream_url = f"{b_url}/movie/{source.username}/{source.password}/{target_movie.source_stream_id}.{ext}"

    if not stream_url:
        raise HTTPException(status_code=502, detail="Upstream movie stream URL not available")

    # Record stream history (IP, title, timestamp)
    client_ip, user_agent = extract_client_info(request)
    record_stream_log(
        db=db,
        stream_type="movie",
        stream_id=movie.id,
        stream_title=movie.display_name or movie.raw_name,
        client_ip=client_ip,
        user_agent=user_agent,
        source_id=target_movie.source_id,
        is_failover=is_failover,
    )

    return RedirectResponse(url=stream_url, status_code=status.HTTP_302_FOUND)


@router.get("/series/{username}/{password}/{stream_spec}")
def proxy_series_stream(
    username: str,
    password: str,
    stream_spec: str,  # e.g. "456.mp4" (episode ID)
    request: Request,
    db: Session = Depends(get_db),
):
    """Redirect series episode request to upstream provider stream URL."""
    verify_stream_auth(username, password)

    episode_id_str = stream_spec.split(".")[0]
    try:
        episode_id = int(episode_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid episode ID format")

    episode = db.query(Episode).filter(Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="Episode not found")

    # Check parent series is not hidden
    series = db.query(Series).filter(Series.id == episode.series_id).first()
    if series and series.is_hidden:
        raise HTTPException(status_code=404, detail="Series is hidden")

    stream_url = episode.stream_url
    if not stream_url:
        source = db.query(Source).filter(Source.id == episode.source_id).first()
        if source and source.type == "xtream":
            b_url = clean_base_url(source.url)
            ext = episode.container_ext or "mp4"
            stream_url = f"{b_url}/series/{source.username}/{source.password}/{episode.source_episode_id}.{ext}"

    if not stream_url:
        raise HTTPException(status_code=502, detail="Upstream series stream URL not available")

    # Record stream history
    client_ip, user_agent = extract_client_info(request)
    record_stream_log(
        db=db,
        stream_type="series",
        stream_id=episode.id,
        stream_title=f"{series.display_name if series else 'Series'} - S{episode.season_num}E{episode.episode_num} ({episode.title})",
        client_ip=client_ip,
        user_agent=user_agent,
        source_id=episode.source_id,
        is_failover=False,
    )

    return RedirectResponse(url=stream_url, status_code=status.HTTP_302_FOUND)


# ── Web UI Preview & Desktop Player Integration ──

def resolve_stream_url(db: Session, stream_type: str, stream_id: int) -> Tuple[str, str, str]:
    """Resolve direct upstream stream URL, title, and container format for a given item."""
    stype = (stream_type or "").lower().strip()
    if stype in ("live", "channel", "channels"):
        channel = db.query(Channel).filter(Channel.id == stream_id).first()
        if not channel:
            raise HTTPException(status_code=404, detail="Channel not found")
        target_channel = channel
        failover = find_failover_channel(db, channel)
        if failover:
            target_channel, _ = failover
        url = target_channel.stream_url
        if not url:
            source = db.query(Source).filter(Source.id == target_channel.source_id).first()
            if source and source.type == "xtream":
                b_url = clean_base_url(source.url)
                url = f"{b_url}/live/{source.username}/{source.password}/{target_channel.source_stream_id}.ts"
        title = target_channel.display_name or target_channel.raw_name
        return url or "", title, "ts"

    elif stype in ("movie", "vod", "movies"):
        movie = db.query(Movie).filter(Movie.id == stream_id).first()
        if not movie:
            raise HTTPException(status_code=404, detail="Movie not found")
        target_movie = movie
        failover = find_failover_movie(db, movie)
        if failover:
            target_movie, _ = failover
        ext = target_movie.container_ext or "mp4"
        url = target_movie.stream_url
        if not url:
            source = db.query(Source).filter(Source.id == target_movie.source_id).first()
            if source and source.type == "xtream":
                b_url = clean_base_url(source.url)
                url = f"{b_url}/movie/{source.username}/{source.password}/{target_movie.source_stream_id}.{ext}"
        title = target_movie.display_name or target_movie.raw_name
        return url or "", title, ext

    elif stype in ("series", "episode", "episodes"):
        episode = db.query(Episode).filter(Episode.id == stream_id).first()
        if not episode:
            raise HTTPException(status_code=404, detail="Episode not found")
        ext = episode.container_ext or "mp4"
        url = episode.stream_url
        if not url:
            source = db.query(Source).filter(Source.id == episode.source_id).first()
            if source and source.type == "xtream":
                b_url = clean_base_url(source.url)
                url = f"{b_url}/series/{source.username}/{source.password}/{episode.source_episode_id}.{ext}"
        title = episode.title or f"Episode {episode.episode_num}"
        return url or "", title, ext

    raise HTTPException(status_code=400, detail="Invalid stream type")


@router.get("/api/proxy/stream-info/{stream_type}/{stream_id}")
def get_stream_info(
    stream_type: str,
    stream_id: int,
    db: Session = Depends(get_db),
    admin: str = Depends(require_admin),
):
    """Return resolved upstream stream URL, title, container format, and MKV flag for the Web UI."""
    url, title, ext = resolve_stream_url(db, stream_type, stream_id)
    ext_lower = ext.lower().strip()
    return {
        "url": url,
        "title": title,
        "container": ext_lower,
        "is_mkv": ext_lower == "mkv",
        "is_ts": ext_lower == "ts",
        "is_m3u8": "m3u8" in url.lower() or ext_lower == "m3u8",
    }


@router.get("/api/proxy/stream-launcher/{stream_type}/{stream_id}")
def stream_launcher(
    stream_type: str,
    stream_id: int,
    db: Session = Depends(get_db),
    admin: str = Depends(require_admin),
):
    """
    Generate a dynamic 1-click .m3u launcher file that immediately opens
    the stream in VLC, IINA, MPV, or PotPlayer with full hardware decoding.
    """
    url, title, ext = resolve_stream_url(db, stream_type, stream_id)
    if not url:
        raise HTTPException(status_code=404, detail="Stream URL not available")

    safe_title = "".join(c for c in title if c.isalnum() or c in (" ", "-", "_")).strip() or "stream"
    m3u_content = f"#EXTM3U\n#EXTINF:-1,{title}\n{url}\n"

    return Response(
        content=m3u_content,
        media_type="audio/x-mpegurl",
        headers={"Content-Disposition": f'attachment; filename="{safe_title}.m3u"'},
    )


@router.get("/api/proxy/stream-preview/{stream_type}/{stream_id}")
async def stream_preview_relay(
    stream_type: str,
    stream_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: str = Depends(require_admin),
):
    """
    Streaming proxy relay strictly for the admin Web UI preview.
    Uses realistic media player User-Agent and injects CORS headers so the browser
    is never blocked by upstream anti-hotlinking checks.
    """
    url, title, ext = resolve_stream_url(db, stream_type, stream_id)
    if not url:
        raise HTTPException(status_code=404, detail="Stream URL not available")

    range_header = request.headers.get("range")
    req_headers = {"User-Agent": "VLC/3.0.18 LibVLC/3.0.18"}
    if range_header:
        req_headers["Range"] = range_header

    client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
    try:
        upstream_req = client.build_request("GET", url, headers=req_headers)
        upstream_resp = await client.send(upstream_req, stream=True)
    except Exception as e:
        await client.aclose()
        raise HTTPException(status_code=502, detail=f"Failed to connect to upstream stream: {e}")

    resp_headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "*",
        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        "Accept-Ranges": "bytes",
    }
    if "content-type" in upstream_resp.headers:
        resp_headers["Content-Type"] = upstream_resp.headers["content-type"]
    if "content-length" in upstream_resp.headers:
        resp_headers["Content-Length"] = upstream_resp.headers["content-length"]
    if "content-range" in upstream_resp.headers:
        resp_headers["Content-Range"] = upstream_resp.headers["content-range"]

    async def stream_generator():
        try:
            async for chunk in upstream_resp.aiter_bytes():
                yield chunk
        finally:
            await upstream_resp.aclose()
            await client.aclose()

    return StreamingResponse(
        stream_generator(),
        status_code=upstream_resp.status_code,
        headers=resp_headers,
    )
