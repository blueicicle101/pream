"""System router — health check, basic system info, stream logs, backup, and onboarding endpoints."""
from datetime import datetime, timezone
import json
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.channel import Channel
from app.models.movie import Movie
from app.models.series import Series
from app.models.setting import Setting
from app.models.source import Source
from app.models.stream_log import StreamLog
from app.services.auth import require_admin
from app.services.backup import export_encrypted_backup, import_encrypted_backup
from app.services.stream_logs import cleanup_old_stream_logs
from app.services.task_status import get_task_status

router = APIRouter(tags=["System"])


@router.get("/api/system/task-progress")
def get_task_progress(admin: str = Depends(require_admin)):
    """Return real-time task progress and status messages for UI footer banner."""
    return get_task_status()


@router.get("/health")
def health_check():
    """Liveness check — always returns OK if the server is running."""
    return {
        "status": "ok",
        "service": settings.SERVER_NAME,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/api/status")
def system_status(db: Session = Depends(get_db)):
    """Detailed status — source counts, library totals, server info."""
    sources = db.query(Source).all()
    source_statuses = []
    for s in sources:
        source_statuses.append({
            "id": s.id,
            "name": s.name,
            "type": s.type,
            "enabled": s.enabled,
            "status": s.last_status,
            "last_refresh": s.last_refresh.isoformat() if s.last_refresh else None,
            "last_error": s.last_error,
        })

    total_channels = db.query(Channel).count()
    total_movies = db.query(Movie).count()
    total_series = db.query(Series).count()

    return {
        "status": "ok",
        "server_name": settings.SERVER_NAME,
        "sources": source_statuses,
        "library": {
            "channels": total_channels,
            "movies": total_movies,
            "series": total_series,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── Stream Logs / Playback History (Auto-clearing 30 days) ──

@router.get("/api/system/stream-logs")
def get_stream_logs(
    stream_type: Optional[str] = Query(None, description="Filter by stream type: movie, live, series"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    admin: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Return recent playback logs with calling IP, title, stream type, and timestamp."""
    query = db.query(StreamLog)
    if stream_type and stream_type.strip():
        query = query.filter(StreamLog.stream_type == stream_type.strip().lower())

    total = query.count()
    logs = (
        query.order_by(StreamLog.created_at.desc(), StreamLog.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    # Aggregates
    movies_count = db.query(StreamLog).filter(StreamLog.stream_type == "movie").count()
    live_count = db.query(StreamLog).filter(StreamLog.stream_type == "live").count()
    series_count = db.query(StreamLog).filter(StreamLog.stream_type == "series").count()
    failover_count = db.query(StreamLog).filter(StreamLog.is_failover == 1).count()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "movies_count": movies_count,
        "live_count": live_count,
        "series_count": series_count,
        "failover_count": failover_count,
        "logs": [
            {
                "id": l.id,
                "stream_type": l.stream_type,
                "stream_id": l.stream_id,
                "stream_title": l.stream_title,
                "client_ip": l.client_ip,
                "user_agent": l.user_agent,
                "source_id": l.source_id,
                "is_failover": bool(l.is_failover),
                "created_at": l.created_at.isoformat() if l.created_at else None,
            }
            for l in logs
        ],
    }


@router.post("/api/system/stream-logs/cleanup")
def cleanup_stream_logs_endpoint(
    days: int = Query(30, ge=1, le=365),
    admin: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Manually trigger 30-day stream history log purge."""
    deleted = cleanup_old_stream_logs(db, days=days)
    return {"status": "ok", "deleted": deleted, "retention_days": days}


# ── Settings Backup & Import (Encrypted JSON) ──

class BackupExportRequest(BaseModel):
    password: Optional[str] = None


@router.post("/api/system/backup/export")
def export_backup_endpoint(
    payload: Optional[BackupExportRequest] = None,
    admin: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Export encrypted JSON backup containing settings, sources, and rules."""
    pwd = (payload.password if payload and payload.password else settings.ADMIN_PASSWORD) or ""
    envelope = export_encrypted_backup(db, pwd)
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"pream_backup_{date_str}.json"

    content = json.dumps(envelope, indent=2)
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/api/system/backup/import")
async def import_backup_endpoint(
    file: Optional[UploadFile] = File(None),
    password: str = Form(""),
    mode: str = Form("merge"),  # "merge" or "replace"
    admin: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Import encrypted JSON backup to restore settings, sources, and rules."""
    if not file:
        raise HTTPException(status_code=400, detail="Backup file is required.")

    try:
        content_bytes = await file.read()
        backup_dict = json.loads(content_bytes.decode("utf-8"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON file: {e}")

    pwd = password.strip() if password else settings.ADMIN_PASSWORD

    try:
        result = import_encrypted_backup(db, backup_dict, pwd, mode=mode)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Restore error: {e}")


# ── Onboarding Wizard ──

@router.post("/api/system/onboarding/dismiss")
def dismiss_onboarding(
    admin: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Dismiss onboarding wizard and record completion in database settings."""
    existing = db.query(Setting).filter(Setting.key == "onboarding_completed").first()
    if existing:
        existing.value = "true"
    else:
        db.add(Setting(key="onboarding_completed", value="true"))
    db.commit()
    return {"status": "ok", "onboarding_completed": True}
