"""ActivityLog model — tracks system events such as source refreshes and TMDB enrichment."""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, Integer, String, Text

from app.database import Base


class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id = Column(Integer, primary_key=True, index=True)
    category = Column(String(50), nullable=False, index=True)  # "refresh", "tmdb", "system"
    event = Column(String(100), nullable=False)  # e.g. "Source Refresh: IPTV", "TMDB Scoped Category"
    status = Column(String(20), nullable=False, default="ok", index=True)  # "ok", "error", "cancelled", "warning"
    details = Column(Text, nullable=True)  # Human-readable summary or JSON details
    duration_ms = Column(Float, nullable=True)  # Execution time in milliseconds
    items_affected = Column(Integer, nullable=True, default=0)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), index=True)

    def __repr__(self):
        return f"<ActivityLog(id={self.id}, cat='{self.category}', event='{self.event}', status='{self.status}')>"

