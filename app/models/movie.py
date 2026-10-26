"""Movie model — represents a VOD movie from a source."""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text

from app.database import Base


class Movie(Base):
    __tablename__ = "movies"

    id = Column(Integer, primary_key=True, index=True)
    source_id = Column(Integer, ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True)
    source_stream_id = Column(String(100), nullable=True)  # Provider's stream ID

    # Raw fields — NEVER modified by rules
    raw_name = Column(Text, nullable=False)
    raw_group = Column(Text, nullable=True)

    # Display fields — computed by rules engine
    display_name = Column(Text, nullable=False, index=True)
    display_group = Column(Text, nullable=True, index=True)  # e.g. "EN | Action"

    # TMDB metadata
    year = Column(Integer, nullable=True)
    tmdb_id = Column(Integer, nullable=True, index=True)
    poster_url = Column(Text, nullable=True)
    backdrop_url = Column(Text, nullable=True)
    plot = Column(Text, nullable=True)
    genre = Column(Text, nullable=True)  # Comma-separated
    rating = Column(Float, nullable=True)

    container_ext = Column(String(20), nullable=True)  # mp4, mkv, etc.
    is_hidden = Column(Boolean, nullable=False, default=False, index=True)
    stream_url = Column(Text, nullable=True)
    tmdb_fetched_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return f"<Movie(id={self.id}, display_name='{self.display_name}')>"
