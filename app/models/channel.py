"""Channel model — represents a Live TV channel from a source."""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text

from app.database import Base


class Channel(Base):
    __tablename__ = "channels"

    id = Column(Integer, primary_key=True, index=True)
    source_id = Column(Integer, ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True)
    source_stream_id = Column(String(100), nullable=True)  # Provider's stream ID

    # Raw fields — NEVER modified by rules
    raw_name = Column(Text, nullable=False)
    raw_group = Column(Text, nullable=True)

    # Display fields — computed by rules engine
    display_name = Column(Text, nullable=False)
    display_group = Column(Text, nullable=True)

    logo_url = Column(Text, nullable=True)
    epg_channel_id = Column(String(255), nullable=True)
    is_hidden = Column(Boolean, nullable=False, default=False)
    stream_url = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return f"<Channel(id={self.id}, display_name='{self.display_name}')>"
