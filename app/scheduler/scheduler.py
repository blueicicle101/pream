"""
APScheduler lifecycle manager for Pream.
Schedules background cron jobs for parallel playlist refreshes and TMDB enrichment.
"""
import logging
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.database import SessionLocal
from app.models.setting import Setting
from app.scheduler.tasks import scheduled_refresh_job, scheduled_tmdb_job

logger = logging.getLogger(__name__)

scheduler: Optional[BackgroundScheduler] = None


def get_cron_expression() -> str:
    """Get active cron expression from DB setting or default config."""
    db = SessionLocal()
    try:
        s = db.query(Setting).filter(Setting.key == "refresh_cron").first()
        if s and s.value:
            return s.value.strip()
    except Exception:
        pass
    finally:
        db.close()
    return settings.REFRESH_CRON.strip()


def start_scheduler():
    """Initialize and start the background scheduler."""
    global scheduler
    if scheduler and scheduler.running:
        return

    scheduler = BackgroundScheduler(daemon=True)

    # 1. Source refresh cron job
    cron_expr = get_cron_expression()
    try:
        cron_trigger = CronTrigger.from_crontab(cron_expr)
        scheduler.add_job(
            scheduled_refresh_job,
            trigger=cron_trigger,
            id="refresh_sources",
            name="Scheduled Source Refresh",
            replace_existing=True,
        )
        logger.info(f"Scheduled source refresh job with cron '{cron_expr}'")
    except Exception as e:
        logger.error(f"Invalid cron expression '{cron_expr}': {e}. Falling back to 0 4 * * *")
        scheduler.add_job(
            scheduled_refresh_job,
            trigger=CronTrigger.from_crontab("0 4 * * *"),
            id="refresh_sources",
            replace_existing=True,
        )

    # 2. TMDB enrichment job (every 15 minutes)
    scheduler.add_job(
        scheduled_tmdb_job,
        trigger=IntervalTrigger(minutes=15),
        id="tmdb_enrichment",
        name="Scheduled TMDB Metadata Enrichment",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("APScheduler background scheduler started.")


def stop_scheduler():
    """Cleanly shut down the background scheduler."""
    global scheduler
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("APScheduler background scheduler stopped.")
