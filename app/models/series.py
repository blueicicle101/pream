"""Series and Episode models — represents a TV series and its episodes from a source."""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text

from app.database import Base


class Series(Base):
    __tablename__ = "series"

    id = Column(Integer, primary_key=True, index=True)
    source_id = Column(Integer, ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True)
    source_series_id = Column(String(100), nullable=True)  # Provider's series ID

    # Raw fields — NEVER modified by rules
    raw_name = Column(Text, nullable=False)
    raw_group = Column(Text, nullable=True)

    # Display fields — computed by rules engine
    display_name = Column(Text, nullable=False)
    display_group = Column(Text, nullable=True)  # e.g. "EN | Drama"

    # TMDB metadata
    year = Column(Integer, nullable=True)
    tmdb_id = Column(Integer, nullable=True, index=True)
    poster_url = Column(Text, nullable=True)
    plot = Column(Text, nullable=True)
    genre = Column(Text, nullable=True)  # Comma-separated
    rating = Column(Float, nullable=True)

    is_hidden = Column(Boolean, nullable=False, default=False)
    tmdb_fetched_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return f"<Series(id={self.id}, display_name='{self.display_name}')>"


class Episode(Base):
    __tablename__ = "episodes"

    id = Column(Integer, primary_key=True, index=True)
    series_id = Column(Integer, ForeignKey("series.id", ondelete="CASCADE"), nullable=False, index=True)
    source_id = Column(Integer, ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True)
    source_episode_id = Column(String(100), nullable=True)  # Provider's episode ID

    season_num = Column(Integer, nullable=True)
    episode_num = Column(Integer, nullable=True)
    title = Column(Text, nullable=True)
    container_ext = Column(String(20), nullable=True)  # mp4, mkv, etc.
    stream_url = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return f"<Episode(id={self.id}, S{self.season_num}E{self.episode_num})>"
