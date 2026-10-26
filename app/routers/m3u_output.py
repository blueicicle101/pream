"""
M3U playlist output router (/get.php).
Generates an M3U / M3U Plus playlist of the cleaned and organized library
pointing back to Pream's stream redirect gateway.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.channel import Channel
from app.models.movie import Movie

router = APIRouter(tags=["M3U Output"])


@router.get("/get.php")
def get_m3u_playlist(
    request: Request,
    username: Optional[str] = Query(None),
    password: Optional[str] = Query(None),
    type: Optional[str] = Query("m3u_plus"),
    output: Optional[str] = Query("ts"),
    db: Session = Depends(get_db),
):
    """
    Generate M3U / M3U_PLUS playlist for IPTV clients.
    """
    if username != settings.OUTPUT_USERNAME or password != settings.OUTPUT_PASSWORD:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid credentials")

    # Determine base URL for playback links
    base_url = str(request.base_url).rstrip("/")

    ext_stream = "ts" if (output and output.lower() == "ts") else "m3u8"

    lines = ["#EXTM3U"]

    # 1. Channels (Live TV)
    channels = (
        db.query(Channel)
        .filter(Channel.is_hidden == False)
        .order_by(Channel.display_group.asc(), Channel.display_name.asc())
        .all()
    )

    for ch in channels:
        epg = ch.epg_channel_id or ""
        logo = ch.logo_url or ""
        group = ch.display_group or "Uncategorized"
        name = ch.display_name or "Channel"

        extinf = f'#EXTINF:-1 tvg-id="{epg}" tvg-name="{name}" tvg-logo="{logo}" group-title="{group}",{name}'
        stream_link = ch.stream_url if ch.stream_url else f"{base_url}/live/{username}/{password}/{ch.id}.{ext_stream}"
        lines.append(extinf)
        lines.append(stream_link)

    # 2. Movies (VOD)
    if type and "live" not in type.lower():
        movies = (
            db.query(Movie)
            .filter(Movie.is_hidden == False)
            .order_by(Movie.display_group.asc(), Movie.display_name.asc())
            .all()
        )
        for mv in movies:
            logo = mv.poster_url or ""
            group = mv.display_group or "Movies"
            name = mv.display_name or "Movie"
            ext = mv.container_ext or "mp4"

            extinf = f'#EXTINF:-1 tvg-name="{name}" tvg-logo="{logo}" group-title="{group}",{name}'
            stream_link = mv.stream_url if mv.stream_url else f"{base_url}/movie/{username}/{password}/{mv.id}.{ext}"
            lines.append(extinf)
            lines.append(stream_link)

    playlist_content = "\n".join(lines) + "\n"
    return Response(
        content=playlist_content,
        media_type="application/x-mpegurl",
        headers={"Content-Disposition": 'attachment; filename="pream.m3u"'},
    )
