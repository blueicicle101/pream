"""StreamLog model — tracks playback history of streamed media with automatic 30-day retention."""
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String

from app.database import Base


class StreamLog(Base):
    __tablename__ = "stream_logs"

    id = Column(Integer, primary_key=True, index=True)
    stream_type = Column(String(20), nullable=False, index=True)  # "movie", "live", "series"
    stream_id = Column(Integer, nullable=False, index=True)
    stream_title = Column(String(255), nullable=False)
    client_ip = Column(String(64), nullable=False)
    user_agent = Column(String(255), nullable=True)
    source_id = Column(Integer, nullable=True)
    is_failover = Column(Integer, default=0)  # 1 if redirected to failover source, 0 otherwise
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    def __repr__(self):
        return f"<StreamLog(id={self.id}, type='{self.stream_type}', title='{self.stream_title}', ip='{self.client_ip}')>"

