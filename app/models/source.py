"""Source model — represents an upstream Xtream or M3U provider."""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from app.database import Base


class Source(Base):
    __tablename__ = "sources"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    type = Column(String(20), nullable=False)  # "xtream" or "m3u"
    url = Column(Text, nullable=False)
    username = Column(String(255), nullable=True)  # Xtream only
    password = Column(String(255), nullable=True)  # Xtream only
    content_types = Column(Text, nullable=False, default='["live","vod","series"]')  # JSON array
    priority = Column(Integer, nullable=False, default=10)  # Lower = preferred
    user_agent = Column(Text, nullable=True)  # Optional sticky UA; NULL = random per refresh
    expiry_date = Column(String(50), nullable=True)  # Unix timestamp or date string from provider
    max_connections = Column(String(20), nullable=True)  # Max concurrent connections allowed
    enabled = Column(Boolean, nullable=False, default=True)
    last_refresh = Column(DateTime, nullable=True)
    last_status = Column(String(20), nullable=True, default="pending")  # ok, error, pending
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    @property
    def expiry_info(self) -> dict:
        """Calculate days remaining and formatted date string for template rendering."""
        if not self.expiry_date or self.expiry_date in ("null", "None", ""):
            return {"days": None, "label": "N/A", "tooltip": "No expiration data", "is_expired": False}
        try:
            ts = int(self.expiry_date)
            if ts <= 0:
                return {"days": None, "label": "Unlimited", "tooltip": "No expiration date", "is_expired": False}
            from datetime import timezone
            exp_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            now_dt = datetime.now(timezone.utc)
            days = (exp_dt - now_dt).days
            date_str = exp_dt.strftime("%Y-%m-%d %H:%M UTC")
            if days > 0:
                label = f"{days}d left"
                is_expired = False
            elif days == 0:
                label = "Expires today"
                is_expired = False
            else:
                label = "Expired"
                is_expired = True
            return {"days": days, "label": label, "tooltip": f"Expires: {date_str}", "is_expired": is_expired}
        except Exception:
            return {"days": None, "label": str(self.expiry_date), "tooltip": str(self.expiry_date), "is_expired": False}

    @property
    def connections_display(self) -> str:
        """Format max connections string."""
        if not self.max_connections or self.max_connections in ("0", "null", "None"):
            return "Unlimited"
        return f"{self.max_connections} conn"

    def __repr__(self):
        return f"<Source(id={self.id}, name='{self.name}', type='{self.type}')>"
