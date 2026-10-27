import logging
import os
from datetime import datetime
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.channel import Channel
from app.models.movie import Movie
from app.models.rule import Rule
from app.models.series import Episode, Series
from app.models.setting import Setting
from app.models.source import Source
from app.models.stream_log import StreamLog
from app.models.activity_log import ActivityLog
from app.services.auth import (
    COOKIE_NAME,
    generate_session_token,
    is_authenticated,
    require_admin,
    verify_credentials,
)
from app.services.ingestion.manager import is_group_by_source_enabled
from app.services.ingestion.user_agents import get_user_agent
from app.services.ingestion.xtream import clean_base_url

logger = logging.getLogger(__name__)

templates_dir = os.path.join(os.path.dirname(__file__), "..", "ui", "templates")
templates = Jinja2Templates(directory=templates_dir)

router = APIRouter(include_in_schema=False)


@router.get("/login", response_class=HTMLResponse)
def page_login(request: Request, next: str = "/"):
    """Render admin login page."""
    if is_authenticated(request):
        return RedirectResponse(url=next or "/", status_code=303)
    error = request.query_params.get("error") is not None
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "server_name": settings.SERVER_NAME,
            "next_url": next or "/",
            "default_username": settings.ADMIN_USERNAME,
            "error": error,
        },
    )


@router.post("/login")
def post_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    """Process admin login submission."""
    if verify_credentials(username, password):
        token = generate_session_token(settings.ADMIN_USERNAME)
        redirect_to = next if next and next.startswith("/") else "/"
        response = RedirectResponse(url=redirect_to, status_code=303)
        response.set_cookie(
            key=COOKIE_NAME,
            value=token,
            httponly=True,
            samesite="lax",
            max_age=86400 * 30,  # 30 days
        )
        return response

    return RedirectResponse(url=f"/login?error=1&next={quote(next)}", status_code=303)


@router.get("/logout")
def page_logout():
    """Clear admin session cookie and redirect to login."""
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(key=COOKIE_NAME)
    return response


def get_runtime_bool_setting(db: Session, key: str, default: bool) -> bool:
    """Helper to read boolean setting from DB or default to config."""
    s = db.query(Setting).filter(Setting.key == key).first()
    if s and s.value:
        return s.value.strip().lower() in ("true", "1", "yes")
    return default


def get_runtime_int_setting(db: Session, key: str, default: int) -> int:
    """Helper to read integer setting from DB or default to config."""
    s = db.query(Setting).filter(Setting.key == key).first()
    if s and s.value:
        try:
            return int(s.value.strip())
        except ValueError:
            pass
    return default


@router.get("/", response_class=HTMLResponse)
def page_dashboard(request: Request, db: Session = Depends(get_db), admin: str = Depends(require_admin)):
    """Render dashboard overview page with performance optimizations."""
    opt_source_counts = get_runtime_bool_setting(db, "opt_dashboard_source_counts", settings.OPT_DASHBOARD_SOURCE_COUNTS)
    opt_total_streams = get_runtime_bool_setting(db, "opt_dashboard_total_streams", settings.OPT_DASHBOARD_TOTAL_STREAMS)
    opt_tmdb_progress = get_runtime_bool_setting(db, "opt_dashboard_tmdb_progress", settings.OPT_DASHBOARD_TMDB_PROGRESS)

    sources = db.query(Source).order_by(Source.priority.asc(), Source.name.asc()).all()
    channel_count = db.query(Channel).filter(Channel.is_hidden == False).count()
    movie_count = db.query(Movie).filter(Movie.is_hidden == False).count()
    series_count = db.query(Series).filter(Series.is_hidden == False).count()

    total_channels = db.query(Channel).count() if opt_total_streams else channel_count
    total_movies = db.query(Movie).count() if opt_total_streams else movie_count
    total_series = db.query(Series).count() if opt_total_streams else series_count
    total_all_streams = total_channels + total_movies + total_series

    # Check for first login onboarding
    onboarding_setting = db.query(Setting).filter(Setting.key == "onboarding_completed").first()
    first_login = (onboarding_setting is None) and (len(sources) == 0)

    # Calculate per-source counts efficiently in 3 grouped queries if enabled
    source_stats = []
    if opt_source_counts and len(sources) > 0:
        live_by_source = dict(
            db.query(Channel.source_id, func.count(Channel.id))
            .filter(Channel.is_hidden == False)
            .group_by(Channel.source_id)
            .all()
        )
        vod_by_source = dict(
            db.query(Movie.source_id, func.count(Movie.id))
            .filter(Movie.is_hidden == False)
            .group_by(Movie.source_id)
            .all()
        )
        series_by_source = dict(
            db.query(Series.source_id, func.count(Series.id))
            .filter(Series.is_hidden == False)
            .group_by(Series.source_id)
            .all()
        )
        for s in sources:
            source_stats.append({
                "source": s,
                "live_count": live_by_source.get(s.id, 0),
                "vod_count": vod_by_source.get(s.id, 0),
                "series_count": series_by_source.get(s.id, 0),
            })
    else:
        for s in sources:
            source_stats.append({
                "source": s,
                "live_count": "-",
                "vod_count": "-",
                "series_count": "-",
            })

    # TMDB Enrichment metrics: enriched vs total VOD & Series
    total_vod_and_series = movie_count + series_count
    if opt_tmdb_progress:
        enriched_movies_count = db.query(Movie).filter(Movie.tmdb_id != None, Movie.is_hidden == False).count()
        enriched_series_count = db.query(Series).filter(Series.tmdb_id != None, Series.is_hidden == False).count()
        total_enriched_count = enriched_movies_count + enriched_series_count
        enrichment_percentage = round((total_enriched_count / max(1, total_vod_and_series)) * 100, 1) if total_vod_and_series > 0 else 0
    else:
        enriched_movies_count = 0
        enriched_series_count = 0
        total_enriched_count = 0
        enrichment_percentage = 0

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "active_page": "dashboard",
            "sources": sources,
            "source_stats": source_stats,
            "channel_count": channel_count,
            "total_channels": total_channels,
            "movie_count": movie_count,
            "total_movies": total_movies,
            "series_count": series_count,
            "total_series": total_series,
            "total_all_streams": total_all_streams,
            "opt_source_counts": opt_source_counts,
            "opt_total_streams": opt_total_streams,
            "opt_tmdb_progress": opt_tmdb_progress,
            "enriched_movies_count": enriched_movies_count,
            "enriched_series_count": enriched_series_count,
            "total_enriched_count": total_enriched_count,
            "total_vod_and_series": total_vod_and_series,
            "enrichment_percentage": enrichment_percentage,
            "first_login": first_login,
            "server_name": settings.SERVER_NAME,
        },
    )



@router.get("/sources", response_class=HTMLResponse)
def page_sources(request: Request, db: Session = Depends(get_db), admin: str = Depends(require_admin)):
    """Render sources management page."""
    sources = db.query(Source).order_by(Source.priority.asc(), Source.name.asc()).all()
    return templates.TemplateResponse(
        request=request,
        name="sources.html",
        context={
            "active_page": "sources",
            "sources": sources,
            "server_name": settings.SERVER_NAME,
        },
    )


@router.get("/library", response_class=HTMLResponse)
def page_library(
    request: Request,
    tab: str = Query("live", pattern="^(live|vod|series)$"),
    group: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(60, ge=10, le=200),
    db: Session = Depends(get_db),
    admin: str = Depends(require_admin),
):
    """Render central media library page."""
    sources = db.query(Source).all()
    source_map = {s.id: s for s in sources}

    opt_library_source_badges = get_runtime_bool_setting(db, "opt_library_source_badges", settings.OPT_LIBRARY_SOURCE_BADGES)
    opt_library_category_counts = get_runtime_bool_setting(db, "opt_library_category_counts", settings.OPT_LIBRARY_CATEGORY_COUNTS)

    items = []
    groups = []
    group_sources_map = {}
    group_counts_map = {}
    total_count = 0

    target_model = Channel if tab == "live" else (Movie if tab == "vod" else Series)

    if opt_library_source_badges:
        groups_raw = (
            db.query(target_model.display_group, target_model.source_id)
            .filter(target_model.is_hidden == False)
            .distinct()
            .order_by(target_model.display_group.asc())
            .all()
        )
        for g_name, s_id in groups_raw:
            if g_name:
                group_sources_map.setdefault(g_name, []).append(s_id)
        groups = sorted(list(group_sources_map.keys()))
    else:
        # High speed: fetch unique groups only without joining source_ids
        groups_raw = (
            db.query(target_model.display_group)
            .filter(target_model.is_hidden == False)
            .distinct()
            .order_by(target_model.display_group.asc())
            .all()
        )
        groups = [g[0] for g in groups_raw if g[0]]

    # Optional: category item counts
    if opt_library_category_counts:
        cat_counts_raw = (
            db.query(target_model.display_group, func.count(target_model.id))
            .filter(target_model.is_hidden == False)
            .group_by(target_model.display_group)
            .all()
        )
        group_counts_map = {g: cnt for g, cnt in cat_counts_raw if g}

    query = db.query(target_model).filter(target_model.is_hidden == False)
    if group:
        query = query.filter(target_model.display_group == group)
    if q:
        query = query.filter(target_model.display_name.ilike(f"%{q}%"))

    total_count = query.count()
    items = query.order_by(target_model.display_name.asc()).offset((page - 1) * page_size).limit(page_size).all()

    total_pages = max(1, (total_count + page_size - 1) // page_size)

    return templates.TemplateResponse(
        request=request,
        name="library.html",
        context={
            "active_page": "library",
            "tab": tab,
            "current_group": group,
            "q": q or "",
            "groups": groups,
            "group_sources_map": group_sources_map,
            "group_counts_map": group_counts_map,
            "opt_library_source_badges": opt_library_source_badges,
            "opt_library_category_counts": opt_library_category_counts,
            "items": items,
            "source_map": source_map,
            "page": page,
            "total_pages": total_pages,
            "total_count": total_count,
            "server_name": settings.SERVER_NAME,
            "output_username": settings.OUTPUT_USERNAME,
            "output_password": settings.OUTPUT_PASSWORD,
        },
    )


@router.get("/search", response_class=HTMLResponse)
def page_search(
    request: Request,
    q: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    admin: str = Depends(require_admin),
):
    """Render unified Central Search page across Live TV, VOD Movies, and TV Series."""
    clean_q = (q or "").strip()
    channels = []
    movies = []
    series = []

    if clean_q:
        search_filter = f"%{clean_q}%"
        channels = (
            db.query(Channel)
            .filter(Channel.is_hidden == False, Channel.display_name.ilike(search_filter))
            .order_by(Channel.display_name.asc())
            .limit(30)
            .all()
        )
        movies = (
            db.query(Movie)
            .filter(Movie.is_hidden == False, Movie.display_name.ilike(search_filter))
            .order_by(Movie.display_name.asc())
            .limit(30)
            .all()
        )
        series = (
            db.query(Series)
            .filter(Series.is_hidden == False, Series.display_name.ilike(search_filter))
            .order_by(Series.display_name.asc())
            .limit(30)
            .all()
        )

    sources = db.query(Source).all()
    source_map = {s.id: s for s in sources}

    return templates.TemplateResponse(
        request=request,
        name="search.html",
        context={
            "active_page": "search",
            "q": clean_q,
            "channels": channels,
            "movies": movies,
            "series": series,
            "source_map": source_map,
            "server_name": settings.SERVER_NAME,
            "output_username": settings.OUTPUT_USERNAME,
            "output_password": settings.OUTPUT_PASSWORD,
        },
    )


@router.get("/logs", response_class=HTMLResponse)
def page_logs(
    request: Request,
    tab: str = Query("all", pattern="^(all|stream|refresh|tmdb)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=10, le=100),
    db: Session = Depends(get_db),
    admin: str = Depends(require_admin),
):
    """Render dedicated system and activity logs page."""
    sources = db.query(Source).all()
    source_map = {s.id: s for s in sources}

    stream_logs = []
    activity_logs = []

    stream_total = db.query(StreamLog).count()
    activity_total = db.query(ActivityLog).count()

    offset = (page - 1) * page_size

    if tab in ("all", "stream"):
        stream_logs = (
            db.query(StreamLog)
            .order_by(StreamLog.created_at.desc(), StreamLog.id.desc())
            .offset(offset if tab == "stream" else 0)
            .limit(page_size if tab == "stream" else 25)
            .all()
        )

    if tab in ("all", "refresh", "tmdb"):
        act_query = db.query(ActivityLog)
        if tab == "refresh":
            act_query = act_query.filter(ActivityLog.category == "refresh")
        elif tab == "tmdb":
            act_query = act_query.filter(ActivityLog.category == "tmdb")

        activity_logs = (
            act_query.order_by(ActivityLog.created_at.desc(), ActivityLog.id.desc())
            .offset(offset if tab != "all" else 0)
            .limit(page_size if tab != "all" else 25)
            .all()
        )

    return templates.TemplateResponse(
        request=request,
        name="logs.html",
        context={
            "active_page": "logs",
            "tab": tab,
            "page": page,
            "page_size": page_size,
            "stream_logs": stream_logs,
            "activity_logs": activity_logs,
            "stream_total": stream_total,
            "activity_total": activity_total,
            "source_map": source_map,
            "server_name": settings.SERVER_NAME,
        },
    )


from app.services.rules_engine import get_rules_diff_preview


@router.get("/rules", response_class=HTMLResponse)
def page_rules(request: Request, db: Session = Depends(get_db), admin: str = Depends(require_admin)):
    """Render rules engine management page with initial 2-column live diff preview."""
    db_locked = False
    try:
        rules = db.query(Rule).order_by(Rule.order.asc(), Rule.id.asc()).all()
        diff_items = get_rules_diff_preview(db, scope="channels", limit=100)
    except Exception as e:
        logger.warning(f"Database busy or locked when accessing rules: {e}")
        rules = []
        diff_items = []
        db_locked = True

    return templates.TemplateResponse(
        request=request,
        name="rules.html",
        context={
            "active_page": "rules",
            "rules": rules,
            "diff_items": diff_items,
            "db_locked": db_locked,
            "server_name": settings.SERVER_NAME,
        },
    )


@router.get("/settings", response_class=HTMLResponse)
def page_settings(request: Request, db: Session = Depends(get_db), admin: str = Depends(require_admin)):
    """Render system settings page."""
    group_by_source = is_group_by_source_enabled(db)

    # Get runtime settings from DB
    from app.services.tmdb import is_tmdb_enabled, get_tmdb_rate_limit, is_tmdb_auto_group_enabled
    from app.services.gemini import get_gemini_api_key, is_staggered_gemini_calls_enabled, get_gemini_rate_delay, is_gemini_enabled
    from app.services.notifications import is_telegram_enabled
    from app.services.ingestion.manager import get_source_scan_threads, get_source_timeout
    from app.scheduler.scheduler import is_refresh_cron_enabled
    tmdb_enabled = is_tmdb_enabled(db)
    tmdb_auto_group = is_tmdb_auto_group_enabled(db)
    tmdb_rate_limit = get_tmdb_rate_limit(db)
    gemini_enabled = is_gemini_enabled(db)
    gemini_api_key = get_gemini_api_key(db)
    staggered_gemini_calls = is_staggered_gemini_calls_enabled(db)
    gemini_rate_delay = get_gemini_rate_delay(db)
    telegram_enabled = is_telegram_enabled(db)
    refresh_cron_enabled = is_refresh_cron_enabled()
    source_scan_threads = get_source_scan_threads(db)
    source_timeout = get_source_timeout(db)

    tmdb_key_setting = db.query(Setting).filter(Setting.key == "tmdb_api_key").first()
    tmdb_api_key = tmdb_key_setting.value if tmdb_key_setting else settings.TMDB_API_KEY

    telegram_token_setting = db.query(Setting).filter(Setting.key == "telegram_bot_token").first()
    telegram_bot_token = telegram_token_setting.value if telegram_token_setting else settings.TELEGRAM_BOT_TOKEN

    telegram_chat_setting = db.query(Setting).filter(Setting.key == "telegram_chat_id").first()
    telegram_chat_id = telegram_chat_setting.value if telegram_chat_setting else settings.TELEGRAM_CHAT_ID

    refresh_cron_setting = db.query(Setting).filter(Setting.key == "refresh_cron").first()
    refresh_cron = refresh_cron_setting.value if refresh_cron_setting else settings.REFRESH_CRON

    sql_timing_setting = db.query(Setting).filter(Setting.key == "sql_timing_enabled").first()
    sql_timing_enabled = (sql_timing_setting.value.lower() == "true") if sql_timing_setting else settings.SQL_TIMING_ENABLED

    sql_threshold_setting = db.query(Setting).filter(Setting.key == "sql_timing_threshold_ms").first()
    sql_timing_threshold_ms = float(sql_threshold_setting.value) if sql_threshold_setting else settings.SQL_TIMING_THRESHOLD_MS

    from app.services.stream_health import get_stream_health_config
    stream_health_enabled, stream_health_count = get_stream_health_config(db)

    from app.services.stream_logs import is_stream_failover_enabled, is_stream_history_enabled
    stream_failover = is_stream_failover_enabled(db)
    stream_history = is_stream_history_enabled(db)

    # Performance & Query Optimization Settings
    opt_dashboard_source_counts = get_runtime_bool_setting(db, "opt_dashboard_source_counts", settings.OPT_DASHBOARD_SOURCE_COUNTS)
    opt_dashboard_total_streams = get_runtime_bool_setting(db, "opt_dashboard_total_streams", settings.OPT_DASHBOARD_TOTAL_STREAMS)
    opt_dashboard_tmdb_progress = get_runtime_bool_setting(db, "opt_dashboard_tmdb_progress", settings.OPT_DASHBOARD_TMDB_PROGRESS)
    opt_library_source_badges = get_runtime_bool_setting(db, "opt_library_source_badges", settings.OPT_LIBRARY_SOURCE_BADGES)
    opt_library_category_counts = get_runtime_bool_setting(db, "opt_library_category_counts", settings.OPT_LIBRARY_CATEGORY_COUNTS)
    opt_task_poll_interval_ms = get_runtime_int_setting(db, "opt_task_poll_interval_ms", settings.OPT_TASK_POLL_INTERVAL_MS)
    opt_run_startup_tasks = get_runtime_bool_setting(db, "opt_run_startup_tasks", settings.OPT_RUN_STARTUP_TASKS)

    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "active_page": "settings",
            "group_by_source": group_by_source,
            "tmdb_enabled": tmdb_enabled,
            "tmdb_auto_group": tmdb_auto_group,
            "tmdb_rate_limit": tmdb_rate_limit,
            "tmdb_api_key": tmdb_api_key,
            "gemini_api_key": gemini_api_key,
            "staggered_gemini_calls": staggered_gemini_calls,
            "gemini_rate_delay": gemini_rate_delay,
            "telegram_bot_token": telegram_bot_token,
            "telegram_chat_id": telegram_chat_id,
            "telegram_enabled": telegram_enabled,
            "gemini_enabled": gemini_enabled,
            "refresh_cron_enabled": refresh_cron_enabled,
            "source_scan_threads": source_scan_threads,
            "source_timeout": source_timeout,
            "refresh_cron": refresh_cron,
            "sql_timing_enabled": sql_timing_enabled,
            "sql_timing_threshold_ms": sql_timing_threshold_ms,
            "stream_health": stream_health_enabled,
            "stream_health_count": stream_health_count,
            "stream_failover": stream_failover,
            "stream_history": stream_history,
            "opt_dashboard_source_counts": opt_dashboard_source_counts,
            "opt_dashboard_total_streams": opt_dashboard_total_streams,
            "opt_dashboard_tmdb_progress": opt_dashboard_tmdb_progress,
            "opt_library_source_badges": opt_library_source_badges,
            "opt_library_category_counts": opt_library_category_counts,
            "opt_task_poll_interval_ms": opt_task_poll_interval_ms,
            "opt_run_startup_tasks": opt_run_startup_tasks,
            "output_username": settings.OUTPUT_USERNAME,
            "output_password": settings.OUTPUT_PASSWORD,
            "server_name": settings.SERVER_NAME,
            "host": settings.HOST,
            "port": settings.PORT,
        },
    )


@router.post("/settings")
def update_settings(
    request: Request,
    group_by_source: Optional[str] = Form(None),
    source_scan_threads: Optional[str] = Form("1"),
    source_timeout: Optional[str] = Form("60.0"),
    stream_health: Optional[str] = Form(None),
    stream_health_count: Optional[str] = Form("4"),
    stream_failover: Optional[str] = Form(None),
    stream_history: Optional[str] = Form(None),
    tmdb_enabled: Optional[str] = Form(None),
    tmdb_auto_group: Optional[str] = Form(None),
    tmdb_rate_limit: Optional[str] = Form("0.05"),
    tmdb_api_key: Optional[str] = Form(""),
    gemini_enabled: Optional[str] = Form(None),
    gemini_api_key: Optional[str] = Form(""),
    staggered_gemini_calls: Optional[str] = Form(None),
    gemini_rate_delay: Optional[str] = Form("4.0"),
    telegram_enabled: Optional[str] = Form(None),
    telegram_bot_token: Optional[str] = Form(""),
    telegram_chat_id: Optional[str] = Form(""),
    refresh_cron_enabled: Optional[str] = Form(None),
    refresh_cron: Optional[str] = Form("0 4 * * *"),
    sql_timing_enabled: Optional[str] = Form(None),
    sql_timing_threshold_ms: Optional[str] = Form("10.0"),
    opt_dashboard_source_counts: Optional[str] = Form(None),
    opt_dashboard_total_streams: Optional[str] = Form(None),
    opt_dashboard_tmdb_progress: Optional[str] = Form(None),
    opt_library_source_badges: Optional[str] = Form(None),
    opt_library_category_counts: Optional[str] = Form(None),
    opt_task_poll_interval_ms: Optional[str] = Form("1500"),
    opt_run_startup_tasks: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    admin: str = Depends(require_admin),
):
    """Save runtime settings to SQLite."""
    gbs_val = "true" if group_by_source else "false"
    cron_en_val = "true" if refresh_cron_enabled else "false"
    sql_t_en_val = "true" if sql_timing_enabled else "false"
    tg_en_val = "true" if telegram_enabled else "false"
    gem_en_val = "true" if gemini_enabled else "false"
    sh_val = "true" if stream_health else "false"
    sf_val = "true" if stream_failover else "false"
    hist_val = "true" if stream_history else "false"
    tmdb_en_val = "true" if tmdb_enabled else "false"
    tmdb_ag_val = "true" if tmdb_auto_group else "false"
    gemini_stag_val = "true" if staggered_gemini_calls else "false"
    opt_dsc_val = "true" if opt_dashboard_source_counts else "false"
    opt_dts_val = "true" if opt_dashboard_total_streams else "false"
    opt_dtp_val = "true" if opt_dashboard_tmdb_progress else "false"
    opt_lsb_val = "true" if opt_library_source_badges else "false"
    opt_lcc_val = "true" if opt_library_category_counts else "false"
    opt_rst_val = "true" if opt_run_startup_tasks else "false"
    try:
        opt_poll_val = str(max(0, int(opt_task_poll_interval_ms or 1500)))
    except ValueError:
        opt_poll_val = "1500"
    try:
        sst_val = str(max(1, min(2, int(source_scan_threads or 1))))
    except ValueError:
        sst_val = "1"
    try:
        sto_val = str(max(5.0, float(source_timeout or 60.0)))
    except ValueError:
        sto_val = "60.0"
    try:
        tmdb_rl_val = str(max(0.0, float(tmdb_rate_limit or 0.05)))
    except ValueError:
        tmdb_rl_val = "0.05"
    try:
        gemini_rd_val = str(max(0.5, float(gemini_rate_delay or 4.0)))
    except ValueError:
        gemini_rd_val = "4.0"
    try:
        sh_count = str(max(1, min(5, int(stream_health_count or 4))))
    except ValueError:
        sh_count = "4"
    try:
        sql_thresh_val = str(max(1.0, float(sql_timing_threshold_ms or 10.0)))
    except ValueError:
        sql_thresh_val = "10.0"

    pairs = [
        ("group_by_source", gbs_val),
        ("source_scan_threads", sst_val),
        ("source_timeout", sto_val),
        ("sql_timing_enabled", sql_t_en_val),
        ("sql_timing_threshold_ms", sql_thresh_val),
        ("refresh_cron_enabled", cron_en_val),
        ("telegram_enabled", tg_en_val),
        ("gemini_enabled", gem_en_val),
        ("stream_health", sh_val),
        ("stream_health_count", sh_count),
        ("stream_failover", sf_val),
        ("stream_history", hist_val),
        ("tmdb_enabled", tmdb_en_val),
        ("tmdb_auto_group", tmdb_ag_val),
        ("tmdb_rate_limit", tmdb_rl_val),
        ("tmdb_api_key", (tmdb_api_key or "").strip()),
        ("gemini_api_key", (gemini_api_key or "").strip()),
        ("staggered_gemini_calls", gemini_stag_val),
        ("gemini_rate_delay", gemini_rd_val),
        ("telegram_bot_token", (telegram_bot_token or "").strip()),
        ("telegram_chat_id", (telegram_chat_id or "").strip()),
        ("refresh_cron", (refresh_cron or "0 4 * * *").strip()),
        ("opt_dashboard_source_counts", opt_dsc_val),
        ("opt_dashboard_total_streams", opt_dts_val),
        ("opt_dashboard_tmdb_progress", opt_dtp_val),
        ("opt_library_source_badges", opt_lsb_val),
        ("opt_library_category_counts", opt_lcc_val),
        ("opt_task_poll_interval_ms", opt_poll_val),
        ("opt_run_startup_tasks", opt_rst_val),
    ]

    for key, val in pairs:
        setting = db.query(Setting).filter(Setting.key == key).first()
        if not setting:
            setting = Setting(key=key, value=val)
            db.add(setting)
        else:
            setting.value = val

    db.commit()

    from app.database import update_sql_timing_settings
    update_sql_timing_settings(
        enabled=(sql_t_en_val == "true"),
        threshold_ms=float(sql_thresh_val)
    )

    return RedirectResponse(url="/settings?saved=1", status_code=303)


@router.get("/api/library/series/{series_id}/episodes")
def get_series_episodes(series_id: int, db: Session = Depends(get_db), admin: str = Depends(require_admin)):
    """Fetch series details and episodes grouped by season for Central Library episodes modal."""
    series = db.query(Series).filter(Series.id == series_id).first()
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")

    episodes = db.query(Episode).filter(Episode.series_id == series.id).order_by(Episode.season_num.asc(), Episode.episode_num.asc()).all()

    # If no episodes in DB and series belongs to an Xtream source, attempt on-demand fetch from provider
    if not episodes and series.source_series_id:
        source = db.query(Source).filter(Source.id == series.source_id).first()
        if source and source.type.lower() == "xtream":
            import httpx
            try:
                base = clean_base_url(source.url)
                u = source.username or ""
                p = source.password or ""
                ua = get_user_agent(source.user_agent)
                url = f"{base}/player_api.php?username={u}&password={p}&action=get_series_info&series_id={series.source_series_id}"
                headers = {"User-Agent": ua, "Accept": "*/*"}

                with httpx.Client(timeout=20.0, headers=headers, follow_redirects=True) as client:
                    resp = client.get(url)
                    if resp.status_code == 200:
                        data = resp.json()
                        info = data.get("info", {}) if isinstance(data, dict) else {}

                        # Update series metadata if missing
                        if isinstance(info, dict):
                            if not series.plot and info.get("plot"):
                                series.plot = info.get("plot")
                            if not series.poster_url and (info.get("cover") or info.get("cover_big")):
                                series.poster_url = info.get("cover") or info.get("cover_big")
                            if not series.genre and info.get("genre"):
                                series.genre = info.get("genre")
                            if not series.rating and info.get("rating_5based"):
                                try:
                                    series.rating = float(info.get("rating_5based"))
                                except Exception:
                                    pass

                        raw_episodes = data.get("episodes", {}) if isinstance(data, dict) else {}
                        new_eps = []
                        if isinstance(raw_episodes, dict):
                            for s_num, ep_list in raw_episodes.items():
                                try:
                                    sn = int(s_num)
                                except ValueError:
                                    sn = 1
                                if isinstance(ep_list, list):
                                    for ep in ep_list:
                                        if not isinstance(ep, dict):
                                            continue
                                        ep_id = str(ep.get("id", ""))
                                        ext = ep.get("container_extension") or ep.get("extension") or "mp4"
                                        stream_url = f"{base}/series/{u}/{p}/{ep_id}.{ext}"
                                        ep_num = ep.get("episode_num") or ep.get("episode") or ep.get("order") or 1
                                        try:
                                            ep_num = int(ep_num)
                                        except Exception:
                                            ep_num = 1
                                        title = ep.get("title") or ep.get("name")
                                        if not title and isinstance(ep.get("info"), dict):
                                            title = ep.get("info", {}).get("name")
                                        new_eps.append(Episode(
                                            series_id=series.id,
                                            source_id=source.id,
                                            source_episode_id=ep_id,
                                            season_num=sn,
                                            episode_num=ep_num,
                                            title=title or f"Episode {ep_num}",
                                            container_ext=ext,
                                            stream_url=stream_url,
                                        ))
                        elif isinstance(raw_episodes, list):
                            for idx, item in enumerate(raw_episodes):
                                if isinstance(item, list):
                                    sn = idx + 1
                                    for ep in item:
                                        if not isinstance(ep, dict):
                                            continue
                                        ep_id = str(ep.get("id", ""))
                                        ext = ep.get("container_extension") or "mp4"
                                        stream_url = f"{base}/series/{u}/{p}/{ep_id}.{ext}"
                                        ep_num = ep.get("episode_num") or 1
                                        try:
                                            ep_num = int(ep_num)
                                        except Exception:
                                            ep_num = 1
                                        new_eps.append(Episode(
                                            series_id=series.id,
                                            source_id=source.id,
                                            source_episode_id=ep_id,
                                            season_num=sn,
                                            episode_num=ep_num,
                                            title=ep.get("title") or f"Episode {ep_num}",
                                            container_ext=ext,
                                            stream_url=stream_url,
                                        ))
                                elif isinstance(item, dict):
                                    sn = item.get("season") or item.get("season_num") or 1
                                    try:
                                        sn = int(sn)
                                    except Exception:
                                        sn = 1
                                    ep_id = str(item.get("id", ""))
                                    ext = item.get("container_extension") or "mp4"
                                    stream_url = f"{base}/series/{u}/{p}/{ep_id}.{ext}"
                                    ep_num = item.get("episode_num") or 1
                                    try:
                                        ep_num = int(ep_num)
                                    except Exception:
                                        ep_num = 1
                                    new_eps.append(Episode(
                                        series_id=series.id,
                                        source_id=source.id,
                                        source_episode_id=ep_id,
                                        season_num=sn,
                                        episode_num=ep_num,
                                        title=item.get("title") or f"Episode {ep_num}",
                                        container_ext=ext,
                                        stream_url=stream_url,
                                    ))

                        if new_eps:
                            db.add_all(new_eps)
                            db.commit()
                            episodes = db.query(Episode).filter(Episode.series_id == series.id).order_by(Episode.season_num.asc(), Episode.episode_num.asc()).all()
            except Exception as e:
                logger.warning(f"Error fetching episodes on-demand for series {series_id}: {e}")

    # Group episodes by season
    seasons_dict: dict = {}
    for ep in episodes:
        sn = ep.season_num or 1
        if sn not in seasons_dict:
            seasons_dict[sn] = []

        # Playback URL: direct stream URL or output proxy
        s_url = ep.stream_url or f"/series/{settings.OUTPUT_USERNAME}/{settings.OUTPUT_PASSWORD}/{ep.id}.{ep.container_ext or 'mp4'}"
        seasons_dict[sn].append({
            "id": ep.id,
            "season_num": sn,
            "episode_num": ep.episode_num or 1,
            "title": ep.title or f"Episode {ep.episode_num or 1}",
            "container_ext": ep.container_ext or "mp4",
            "stream_url": s_url,
        })

    return {
        "id": series.id,
        "name": series.display_name,
        "plot": series.plot or "No plot summary available.",
        "genre": series.genre,
        "year": series.year,
        "rating": series.rating,
        "poster_url": series.poster_url,
        "seasons": seasons_dict,
        "total_episodes": len(episodes),
    }

