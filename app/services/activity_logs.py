"""Activity logging service — records source refreshes and TMDB enrichment runs with 30-day retention."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.activity_log import ActivityLog

logger = logging.getLogger(__name__)


def log_activity(
    db: Session,
    category: str,
    event: str,
    status: str = "ok",
    details: Optional[str] = None,
    duration_ms: Optional[float] = None,
    items_affected: Optional[int] = 0,
) -> Optional[ActivityLog]:
    """Record an operational activity log entry (e.g. source refresh or TMDB enrichment run)."""
    try:
        entry = ActivityLog(
            category=category,
            event=event,
            status=status,
            details=details,
            duration_ms=round(duration_ms, 2) if duration_ms is not None else None,
            items_affected=items_affected or 0,
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        db.add(entry)
        db.commit()
        return entry
    except Exception as e:
        logger.warning(f"Failed to write activity log: {e}")
        db.rollback()
        return None


def cleanup_old_activity_logs(db: Session, days: int = 30) -> int:
    """Purge activity logs older than specified days."""
    try:
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        deleted = (
            db.query(ActivityLog)
            .filter(ActivityLog.created_at < cutoff)
            .delete(synchronize_session=False)
        )
        db.commit()
        if deleted > 0:
            logger.info(f"Purged {deleted} activity logs older than {days} days.")
        return deleted
    except Exception as e:
        logger.warning(f"Failed to purge activity logs: {e}")
        db.rollback()
        return 0

