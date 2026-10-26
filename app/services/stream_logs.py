"""
Stream logging and transparent zero-bandwidth failover resolution.
"""
from datetime import datetime, timedelta, timezone
import logging
import random
from typing import Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models.channel import Channel
from app.models.movie import Movie
from app.models.setting import Setting
from app.models.source import Source
from app.models.stream_log import StreamLog

logger = logging.getLogger(__name__)


def is_stream_history_enabled(db: Session) -> bool:
    """Check if stream logging is enabled in DB settings or .env config."""
    db_setting = db.query(Setting).filter(Setting.key == "stream_history").first()
    if db_setting and db_setting.value:
        return db_setting.value.lower() in ("true", "1", "yes")
    return settings.STREAM_HISTORY


def is_stream_failover_enabled(db: Session) -> bool:
    """Check if stream failover is enabled in DB settings or .env config."""
    db_setting = db.query(Setting).filter(Setting.key == "stream_failover").first()
    if db_setting and db_setting.value:
        return db_setting.value.lower() in ("true", "1", "yes")
    return settings.STREAM_FAILOVER


def record_stream_log(
    db: Session,
    stream_type: str,
    stream_id: int,
    stream_title: str,
    client_ip: str,
    user_agent: Optional[str] = None,
    source_id: Optional[int] = None,
    is_failover: bool = False,
):
    """Record a playback event in StreamLog and trigger periodic 30-day cleanup."""
    if not is_stream_history_enabled(db):
        return

    try:
        log_entry = StreamLog(
            stream_type=stream_type,
            stream_id=stream_id,
            stream_title=(stream_title or "Unknown")[:255],
            client_ip=(client_ip or "unknown")[:64],
            user_agent=(user_agent or "")[:255],
            source_id=source_id,
            is_failover=1 if is_failover else 0,
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        db.add(log_entry)
        db.commit()

        # Opportunistic cleanup (approx 1 in 20 requests) to keep table within 30 days
        # Skip random opportunistic check during unit tests (STREAM_HEALTH_COUNT or specific test DB)
        if random.random() < 0.05 and not str(db.bind.url).endswith("test_features.db"):
            cleanup_old_stream_logs(db, days=30)
    except Exception as e:
        logger.warning(f"Failed to record stream log: {e}")
        db.rollback()


def cleanup_old_stream_logs(db: Session, days: int = 30) -> int:
    """Delete stream logs older than specified days (default 30 days)."""
    try:
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        deleted = (
            db.query(StreamLog)
            .filter(StreamLog.created_at < cutoff)
            .delete(synchronize_session=False)
        )
        db.commit()
        if deleted > 0:
            logger.info(f"Purged {deleted} stream playback logs older than {days} days.")
        return deleted
    except Exception as e:
        logger.warning(f"Failed to purge stream logs: {e}")
        db.rollback()
        return 0


def find_failover_channel(
    db: Session, channel: Channel
) -> Optional[Tuple[Channel, Source]]:
    """
    If primary source is in error or disabled, search active healthy sources
    for a matching channel (by epg_channel_id or normalized display_name).
    Returns (alternate_channel, alternate_source) or None.
    """
    if not is_stream_failover_enabled(db):
        return None

    primary_source = (
        db.query(Source).filter(Source.id == channel.source_id).first()
    )
    # If primary source is healthy, no failover needed
    if primary_source and primary_source.enabled and primary_source.last_status != "error":
        return None

    # Search for alternative channel on healthy sources
    healthy_sources = (
        db.query(Source)
        .filter(
            Source.id != channel.source_id,
            Source.enabled == True,
            Source.last_status != "error",
        )
        .order_by(Source.priority.asc())
        .all()
    )
    if not healthy_sources:
        return None

    healthy_source_ids = [s.id for s in healthy_sources]

    # Attempt 1: match by epg_channel_id if present
    if channel.epg_channel_id:
        alt_ch = (
            db.query(Channel)
            .filter(
                Channel.source_id.in_(healthy_source_ids),
                Channel.is_hidden == False,
                Channel.epg_channel_id == channel.epg_channel_id,
            )
            .first()
        )
        if alt_ch:
            alt_src = next((s for s in healthy_sources if s.id == alt_ch.source_id), None)
            if alt_src:
                return alt_ch, alt_src

    # Attempt 2: match by normalized display name
    norm_name = (channel.display_name or channel.raw_name or "").strip().lower()
    if norm_name:
        alt_ch = (
            db.query(Channel)
            .filter(
                Channel.source_id.in_(healthy_source_ids),
                Channel.is_hidden == False,
                func.lower(Channel.display_name) == norm_name,
            )
            .first()
        )
        if alt_ch:
            alt_src = next((s for s in healthy_sources if s.id == alt_ch.source_id), None)
            if alt_src:
                return alt_ch, alt_src

    return None


def find_failover_movie(
    db: Session, movie: Movie
) -> Optional[Tuple[Movie, Source]]:
    """
    If primary source is in error or disabled, search active healthy sources
    for a matching movie (by tmdb_id or normalized display_name).
    Returns (alternate_movie, alternate_source) or None.
    """
    if not is_stream_failover_enabled(db):
        return None

    primary_source = (
        db.query(Source).filter(Source.id == movie.source_id).first()
    )
    if primary_source and primary_source.enabled and primary_source.last_status != "error":
        return None

    healthy_sources = (
        db.query(Source)
        .filter(
            Source.id != movie.source_id,
            Source.enabled == True,
            Source.last_status != "error",
        )
        .order_by(Source.priority.asc())
        .all()
    )
    if not healthy_sources:
        return None

    healthy_source_ids = [s.id for s in healthy_sources]

    # Attempt 1: match by tmdb_id if present
    if movie.tmdb_id:
        alt_mv = (
            db.query(Movie)
            .filter(
                Movie.source_id.in_(healthy_source_ids),
                Movie.is_hidden == False,
                Movie.tmdb_id == movie.tmdb_id,
            )
            .first()
        )
        if alt_mv:
            alt_src = next((s for s in healthy_sources if s.id == alt_mv.source_id), None)
            if alt_src:
                return alt_mv, alt_src

    # Attempt 2: match by normalized display name
    norm_name = (movie.display_name or movie.raw_name or "").strip().lower()
    if norm_name:
        alt_mv = (
            db.query(Movie)
            .filter(
                Movie.source_id.in_(healthy_source_ids),
                Movie.is_hidden == False,
                func.lower(Movie.display_name) == norm_name,
            )
            .first()
        )
        if alt_mv:
            alt_src = next((s for s in healthy_sources if s.id == alt_mv.source_id), None)
            if alt_src:
                return alt_mv, alt_src

    return None
