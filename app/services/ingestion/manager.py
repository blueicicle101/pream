"""
Parallel ingestion manager for Pream.
Coordinates concurrent updates across multiple Xtream and M3U sources,
handles database transactions, error tracking, and source-prefix grouping.
"""
import asyncio
import concurrent.futures
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models.channel import Channel
from app.models.movie import Movie
from app.models.rule import Rule
from app.models.series import Episode, Series
from app.models.setting import Setting
from app.models.source import Source
from app.services.ingestion.m3u import fetch_m3u
from app.services.ingestion.xtream import fetch_xtream
from app.services.rules_engine import (
    apply_all_rules_to_library,
    apply_rules_to_item_chain,
    compile_rules,
)
from app.services.task_status import (
    clear_all_workers,
    remove_worker,
    set_task_status,
    update_worker_progress,
)

logger = logging.getLogger(__name__)

_scan_executor: Optional[concurrent.futures.ThreadPoolExecutor] = None
_db_write_lock = threading.Lock()



def get_scan_executor() -> concurrent.futures.ThreadPoolExecutor:
    """Get or create bounded ThreadPoolExecutor for background source scanning."""
    global _scan_executor
    if _scan_executor is None:
        workers = max(1, getattr(settings, "SOURCE_SCAN_THREADS", 2))
        _scan_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="PreamScanWorker",
        )
    return _scan_executor


def run_refresh_source_background(source_id: int) -> None:
    """Execute refresh_source on an isolated worker thread without blocking the main event loop."""
    def _worker():
        try:
            asyncio.run(refresh_source(source_id))
        except Exception as e:
            logger.exception(f"Background worker error refreshing source {source_id}: {e}")

    executor = get_scan_executor()
    executor.submit(_worker)


def run_refresh_all_background() -> None:
    """Execute refresh_all_sources on an isolated worker thread without blocking the main event loop."""
    def _worker():
        try:
            asyncio.run(refresh_all_sources())
        except Exception as e:
            logger.exception(f"Background worker error refreshing all sources: {e}")

    executor = get_scan_executor()
    executor.submit(_worker)


def is_group_by_source_enabled(db: Session) -> bool:
    """Check if group_by_source is enabled in DB settings or .env config."""
    db_setting = db.query(Setting).filter(Setting.key == "group_by_source").first()
    if db_setting and db_setting.value:
        return db_setting.value.lower() in ("true", "1", "yes")
    return settings.GROUP_BY_SOURCE



async def refresh_source(source_id: int, db: Optional[Session] = None) -> Dict[str, Any]:
    """
    Refresh a single source by fetching content from the provider,
    clearing previous entries, and inserting fresh data with display fields initialized.
    """
    should_close_db = False
    if db is None:
        db = SessionLocal()
        should_close_db = True

    try:
        source = db.query(Source).filter(Source.id == source_id).first()
        if not source:
            return {
                "source_id": source_id,
                "source_name": "Unknown",
                "status": "error",
                "error_reason": "not_found",
                "error_message": f"Source {source_id} not found in database",
            }

        update_worker_progress(str(source_id), source.name, "Connecting...", 15)

        # Fetch data based on source type
        if source.type.lower() == "xtream":
            result = await fetch_xtream(source)
        elif source.type.lower() == "m3u":
            result = await fetch_m3u(source)
        else:
            set_task_status("error", "Error", f"Unsupported type for '{source.name}': {source.type}", 100)
            return {
                "source_id": source.id,
                "source_name": source.name,
                "status": "error",
                "error_reason": "unsupported_type",
                "error_message": f"Unsupported source type: {source.type}",
            }

        now = datetime.now(timezone.utc)

        if result.status != "ok":
            set_task_status(
                "error",
                "Error",
                f"{source.name}: {result.error_reason or 'error'} ({result.error_message or 'Failed'})",
                100,
            )
            source.last_status = "error"
            source.last_error = f"{result.error_reason or 'error'}: {result.error_message or 'Unknown error'}"
            source.last_refresh = now
            db.commit()

            # Send immediate failure alert to Telegram
            try:
                from app.services.notifications import notify_source_failure
                asyncio.create_task(
                    notify_source_failure(source.name, result.error_reason or "error", result.error_message or "", db)
                )
            except Exception as e:
                logger.warning(f"Could not send Telegram failure alert for {source.name}: {e}")

            return {
                "source_id": source.id,
                "source_name": source.name,
                "status": "error",
                "error_reason": result.error_reason,
                "error_message": result.error_message,
                "channels": 0,
                "movies": 0,
                "series": 0,
            }

        group_prefix = is_group_by_source_enabled(db)

        # Pre-compile active rules for instantaneous in-memory transformation
        active_rules = db.query(Rule).filter(Rule.enabled == True).order_by(Rule.order.asc()).all()
        compiled_rules = compile_rules(active_rules)

        # Prepare Channels with rules applied immediately
        channels_to_insert = []
        for ch in result.channels:
            raw_grp = ch.get("raw_group") or "Uncategorized"
            raw_nm = ch.get("raw_name") or "Unnamed Channel"
            disp_nm, disp_grp, is_hid = apply_rules_to_item_chain(
                "live", raw_nm, raw_grp, source.name, group_prefix, compiled_rules
            )
            channels_to_insert.append(
                Channel(
                    source_id=source.id,
                    source_stream_id=ch.get("source_stream_id"),
                    raw_name=raw_nm,
                    raw_group=raw_grp,
                    display_name=disp_nm,
                    display_group=disp_grp,
                    logo_url=ch.get("logo_url"),
                    epg_channel_id=ch.get("epg_channel_id"),
                    stream_url=ch.get("stream_url"),
                    is_hidden=is_hid,
                )
            )

        # Prepare Movies with rules applied immediately
        movies_to_insert = []
        for mv in result.movies:
            raw_grp = mv.get("raw_group") or "Uncategorized"
            raw_nm = mv.get("raw_name") or "Unnamed Movie"
            disp_nm, disp_grp, is_hid = apply_rules_to_item_chain(
                "vod", raw_nm, raw_grp, source.name, group_prefix, compiled_rules
            )
            movies_to_insert.append(
                Movie(
                    source_id=source.id,
                    source_stream_id=mv.get("source_stream_id"),
                    raw_name=raw_nm,
                    raw_group=raw_grp,
                    display_name=disp_nm,
                    display_group=disp_grp,
                    poster_url=mv.get("poster_url"),
                    rating=mv.get("rating"),
                    container_ext=mv.get("container_ext") or "mp4",
                    stream_url=mv.get("stream_url"),
                    is_hidden=is_hid,
                )
            )

        # Prepare Series with rules applied immediately
        series_to_insert = []
        for s in result.series:
            raw_grp = s.get("raw_group") or "Uncategorized"
            raw_nm = s.get("raw_name") or "Unnamed Series"
            disp_nm, disp_grp, is_hid = apply_rules_to_item_chain(
                "series", raw_nm, raw_grp, source.name, group_prefix, compiled_rules
            )
            series_to_insert.append(
                Series(
                    source_id=source.id,
                    source_series_id=s.get("source_series_id"),
                    raw_name=raw_nm,
                    raw_group=raw_grp,
                    display_name=disp_nm,
                    display_group=disp_grp,
                    poster_url=s.get("poster_url"),
                    plot=s.get("plot"),
                    genre=s.get("genre"),
                    year=s.get("year"),
                    rating=s.get("rating"),
                    is_hidden=is_hid,
                )
            )

        update_worker_progress(
            str(source_id),
            source.name,
            f"Saving {len(channels_to_insert)} TV, {len(movies_to_insert)} VOD...",
            70,
        )

        # Thread-safe SQLite atomic write
        with _db_write_lock:
            db.query(Channel).filter(Channel.source_id == source.id).delete()
            db.query(Movie).filter(Movie.source_id == source.id).delete()
            db.query(Episode).filter(Episode.source_id == source.id).delete()
            db.query(Series).filter(Series.source_id == source.id).delete()

            if channels_to_insert:
                db.bulk_save_objects(channels_to_insert)
            if movies_to_insert:
                db.bulk_save_objects(movies_to_insert)
            if series_to_insert:
                db.bulk_save_objects(series_to_insert)

            source.expiry_date = result.expiry_date
            source.max_connections = result.max_connections
            source.last_status = "ok"
            source.last_error = None
            source.last_refresh = now
            db.commit()

        update_worker_progress(str(source_id), source.name, "Complete", 100)

        return {
            "source_id": source.id,
            "source_name": source.name,
            "status": "ok",
            "channels": len(channels_to_insert),
            "movies": len(movies_to_insert),
            "series": len(series_to_insert),
        }

    except Exception as e:
        db.rollback()
        logger.exception(f"Unhandled error refreshing source {source_id}: {e}")
        try:
            src = db.query(Source).filter(Source.id == source_id).first()
            if src:
                src.last_status = "error"
                src.last_error = f"unhandled_error: {str(e)}"
                src.last_refresh = datetime.now(timezone.utc)
                db.commit()
        except Exception:
            pass

        return {
            "source_id": source_id,
            "source_name": f"Source {source_id}",
            "status": "error",
            "error_reason": "unhandled_error",
            "error_message": str(e),
            "channels": 0,
            "movies": 0,
            "series": 0,
        }
    finally:
        remove_worker(str(source_id))
        if should_close_db:
            db.close()


async def refresh_all_sources(db: Optional[Session] = None) -> List[Dict[str, Any]]:
    """
    Refresh all enabled sources concurrently using asyncio.gather.
    """
    should_close_db = False
    if db is None:
        db = SessionLocal()
        should_close_db = True

    try:
        sources = db.query(Source).filter(Source.enabled == True).all()
        source_ids = [s.id for s in sources]
    finally:
        if should_close_db:
            db.close()

    if not source_ids:
        return []

    import time
    start_time = time.time()
    scan_threads = max(1, getattr(settings, "SOURCE_SCAN_THREADS", 2))
    set_task_status("running", "Scanning Sources", f"Scanning {len(source_ids)} sources ({scan_threads} workers)...", 20)

    # Throttle concurrency to SOURCE_SCAN_THREADS
    sem = asyncio.Semaphore(scan_threads)

    async def _bounded_refresh(sid: int):
        async with sem:
            return await refresh_source(sid)

    tasks = [_bounded_refresh(sid) for sid in source_ids]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    formatted_results = []
    for sid, res in zip(source_ids, results):
        if isinstance(res, Exception):
            formatted_results.append({
                "source_id": sid,
                "status": "error",
                "error_reason": "task_exception",
                "error_message": str(res),
            })
        elif isinstance(res, dict):
            formatted_results.append(res)

    # Recount and synchronize affected_count for all rules across library
    try:
        final_db = SessionLocal()
        try:
            with _db_write_lock:
                apply_all_rules_to_library(final_db)
        finally:
            final_db.close()
    except Exception as e:
        logger.warning(f"Could not synchronize rule counters after refresh all: {e}")

    duration = time.time() - start_time
    clear_all_workers()
    set_task_status("completed", "Complete", f"All {len(source_ids)} sources updated in {duration:.1f}s", 100)

    # Send summary report to Telegram
    try:
        from app.services.notifications import notify_refresh_report
        asyncio.create_task(notify_refresh_report(formatted_results, duration))
    except Exception as e:
        logger.warning(f"Could not send Telegram refresh report: {e}")

    return formatted_results
