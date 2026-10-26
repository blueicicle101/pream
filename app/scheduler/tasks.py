"""
Scheduled cron tasks for Pream.
Executed asynchronously by APScheduler in background workers.
"""
import asyncio
import logging

from app.database import SessionLocal
from app.services.ingestion.manager import refresh_all_sources
from app.services.rules_engine import apply_all_rules_to_library
from app.services.tmdb import enrich_pending_media

logger = logging.getLogger(__name__)


def scheduled_refresh_job():
    """Cron job: refresh all sources in parallel and re-apply transformation rules."""
    logger.info("Executing scheduled refresh for all sources...")
    db = SessionLocal()
    try:
        # Run async refresh in a new or existing event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            results = loop.run_until_complete(refresh_all_sources())
            logger.info(f"Scheduled refresh completed: {len(results)} sources processed.")
            # Re-apply rules across the fresh library
            stats = apply_all_rules_to_library(db)
            logger.info(f"Rules re-applied after scheduled refresh: {stats}")
        finally:
            loop.close()
    except Exception as e:
        logger.exception(f"Error during scheduled refresh job: {e}")
    finally:
        db.close()


def scheduled_tmdb_job():
    """Recurring job: enrich pending movies and TV series with TMDB metadata."""
    logger.info("Executing scheduled TMDB enrichment job...")
    db = SessionLocal()
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            res = loop.run_until_complete(enrich_pending_media(db, batch_size=100, continuous=False, max_items=200))
            logger.info(f"Scheduled TMDB job result: {res}")
        finally:
            loop.close()
    except Exception as e:
        logger.exception(f"Error during scheduled TMDB job: {e}")
    finally:
        db.close()
