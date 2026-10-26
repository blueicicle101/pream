"""
Source management API router.
Provides CRUD endpoints and refresh triggers for IPTV sources.
"""
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.channel import Channel
from app.models.movie import Movie
from app.models.series import Episode, Series
from app.models.source import Source
from app.schemas.source import (
    SourceCreate,
    SourceRead,
    SourceTestRequest,
    SourceTestResponse,
    SourceUpdate,
)
from app.services.auth import require_admin
from app.services.ingestion.manager import (
    refresh_all_sources,
    refresh_source,
    run_refresh_all_background,
    run_refresh_source_background,
)
from app.services.ingestion.user_agents import get_user_agent
from app.services.ingestion.xtream import clean_base_url

router = APIRouter(prefix="/api/sources", tags=["Sources"], dependencies=[Depends(require_admin)])


@router.post("/test", response_model=SourceTestResponse)
async def test_source_connection(req: SourceTestRequest):
    """
    Test connectivity and credentials against an upstream Xtream or M3U provider,
    returning account status, expiry date, days remaining, and max connections.
    """
    if req.type.lower() == "xtream":
        base_url = clean_base_url(req.url)
        username = req.username or ""
        password = req.password or ""
        ua = get_user_agent(req.user_agent)
        headers = {"User-Agent": ua, "Accept": "*/*"}
        timeout = httpx.Timeout(connect=10.0, read=20.0, write=10.0, pool=10.0)

        auth_url = f"{base_url}/player_api.php?username={username}&password={password}"
        try:
            async with httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=True) as client:
                resp = await client.get(auth_url)
                if resp.status_code in (401, 403):
                    return SourceTestResponse(
                        status="error",
                        message=f"Authentication failed: HTTP {resp.status_code} - Invalid username or password",
                    )
                if resp.status_code == 404:
                    return SourceTestResponse(
                        status="error",
                        message=f"Endpoint not found: HTTP 404 at {base_url}/player_api.php",
                    )
                if resp.status_code != 200:
                    return SourceTestResponse(
                        status="error",
                        message=f"HTTP {resp.status_code} - Unexpected server response",
                    )

                try:
                    data = resp.json()
                except Exception as e:
                    return SourceTestResponse(
                        status="error",
                        message=f"Invalid JSON response from provider: {str(e)}",
                    )

                user_info = data.get("user_info", {}) if isinstance(data, dict) else {}
                auth_val = user_info.get("auth")
                status_val = user_info.get("status")

                if auth_val == 0 or (status_val and status_val.lower() not in ("active", "trial")):
                    msg = user_info.get("message") or f"Account status is '{status_val}'"
                    return SourceTestResponse(
                        status="error",
                        message=f"Authentication rejected by provider: {msg}",
                    )

                exp_raw = user_info.get("exp_date")
                exp_str = str(exp_raw) if exp_raw is not None else None
                max_conns = str(user_info.get("max_connections")) if user_info.get("max_connections") is not None else None
                active_conns = str(user_info.get("active_cons")) if user_info.get("active_cons") is not None else "0"

                # Calculate days remaining and formatted date
                days_rem = None
                formatted_exp = None
                if exp_str and exp_str not in ("null", "None", ""):
                    try:
                        ts = int(exp_str)
                        if ts > 0:
                            exp_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                            now_dt = datetime.now(timezone.utc)
                            days_rem = (exp_dt - now_dt).days
                            formatted_exp = exp_dt.strftime("%Y-%m-%d %H:%M UTC")
                        else:
                            formatted_exp = "Unlimited"
                    except Exception:
                        formatted_exp = exp_str

                return SourceTestResponse(
                    status="ok",
                    message="Connection successful! Provider authenticated.",
                    expiry_date=exp_str,
                    days_remaining=days_rem,
                    formatted_expiry=formatted_exp,
                    max_connections=max_conns,
                    active_connections=active_conns,
                    account_status=status_val or "Active",
                )

        except httpx.ConnectTimeout:
            return SourceTestResponse(status="error", message="Connection timed out while connecting to provider host.")
        except httpx.ConnectError as e:
            return SourceTestResponse(status="error", message=f"Failed to connect to provider host: {str(e)}")
        except Exception as e:
            return SourceTestResponse(status="error", message=f"Connection test failed: {str(e)}")

    elif req.type.lower() == "m3u":
        ua = get_user_agent(req.user_agent)
        headers = {"User-Agent": ua, "Accept": "*/*"}
        timeout = httpx.Timeout(connect=10.0, read=20.0, write=10.0, pool=10.0)
        try:
            async with httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=True) as client:
                resp = await client.get(req.url, headers={"Range": "bytes=0-1024"})
                if resp.status_code in (200, 206):
                    return SourceTestResponse(
                        status="ok",
                        message="Connection successful! M3U playlist is reachable (HTTP 200/206).",
                    )
                return SourceTestResponse(
                    status="error",
                    message=f"HTTP {resp.status_code} returned by M3U provider.",
                )
        except Exception as e:
            return SourceTestResponse(status="error", message=f"Failed to reach M3U playlist: {str(e)}")

    return SourceTestResponse(status="error", message=f"Unsupported source type: {req.type}")


@router.post("/{source_id}/refresh")
def trigger_refresh_source(source_id: int, db: Session = Depends(get_db)):
    """Trigger an immediate non-blocking refresh for a specific source in a background thread."""
    source = db.query(Source).filter(Source.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    run_refresh_source_background(source_id)
    return {
        "status": "started",
        "message": f"Refresh started in background for '{source.name}'",
        "source_id": source.id,
        "source_name": source.name,
    }


@router.post("/refresh")
def trigger_refresh_all():
    """Trigger non-blocking parallel refresh for all enabled sources in background threads."""
    run_refresh_all_background()
    return {
        "status": "started",
        "message": "Parallel refresh for all enabled sources started in background",
    }


@router.post("/health-check")
async def trigger_stream_health_check(
    count: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """
    Test random streams across enabled sources without downloading full files,
    re-prioritize sources based on success rate, and report to Telegram channel.
    """
    from app.services.stream_health import run_stream_health_check
    result = await run_stream_health_check(db, custom_count=count)
    return result


@router.get("", response_model=List[SourceRead])
def list_sources(db: Session = Depends(get_db)):
    """List all configured sources ordered by priority and name."""
    return db.query(Source).order_by(Source.priority.asc(), Source.name.asc()).all()


@router.post("", response_model=SourceRead, status_code=status.HTTP_201_CREATED)
def create_source(source_in: SourceCreate, db: Session = Depends(get_db)):
    """Create a new source."""
    content_types_json = json.dumps(source_in.content_types)

    db_source = Source(
        name=source_in.name,
        type=source_in.type.lower(),
        url=source_in.url,
        username=source_in.username,
        password=source_in.password,
        content_types=content_types_json,
        priority=source_in.priority,
        user_agent=source_in.user_agent,
        enabled=source_in.enabled,
    )
    db.add(db_source)
    db.commit()
    db.refresh(db_source)
    return db_source


@router.get("/{source_id}", response_model=SourceRead)
def get_source(source_id: int, db: Session = Depends(get_db)):
    """Get single source by ID."""
    source = db.query(Source).filter(Source.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    return source


@router.put("/{source_id}", response_model=SourceRead)
def update_source(source_id: int, source_in: SourceUpdate, db: Session = Depends(get_db)):
    """Update source properties."""
    source = db.query(Source).filter(Source.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    update_data = source_in.model_dump(exclude_unset=True)

    if "content_types" in update_data and update_data["content_types"] is not None:
        source.content_types = json.dumps(update_data["content_types"])
        del update_data["content_types"]

    for key, value in update_data.items():
        setattr(source, key, value)

    db.commit()
    db.refresh(source)
    return source


@router.get("/{source_id}/counts")
def get_source_item_counts(source_id: int, db: Session = Depends(get_db)):
    """Return count of channels, movies, and series associated with a source."""
    source = db.query(Source).filter(Source.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    channels_count = db.query(Channel).filter(Channel.source_id == source_id).count()
    movies_count = db.query(Movie).filter(Movie.source_id == source_id).count()
    series_count = db.query(Series).filter(Series.source_id == source_id).count()

    return {
        "source_id": source.id,
        "source_name": source.name,
        "channels": channels_count,
        "movies": movies_count,
        "series": series_count,
        "total": channels_count + movies_count + series_count,
    }


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(source_id: int, db: Session = Depends(get_db)):
    """Delete a source and cascade remove all its channels, movies, and series."""
    source = db.query(Source).filter(Source.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    # Clean up associated items
    db.query(Channel).filter(Channel.source_id == source_id).delete()
    db.query(Movie).filter(Movie.source_id == source_id).delete()
    db.query(Episode).filter(Episode.source_id == source_id).delete()
    db.query(Series).filter(Series.source_id == source_id).delete()

    db.delete(source)
    db.commit()
    return None
