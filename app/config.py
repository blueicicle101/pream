"""
Pream Configuration — loads from .env file via Pydantic BaseSettings.
"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env file."""

    # ── Admin UI Auth ──
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "pream"

    # ── Output Xtream Credentials (what IPTV clients connect with) ──
    OUTPUT_USERNAME: str = "pream"
    OUTPUT_PASSWORD: str = "pream"

    # ── TMDB ──
    TMDB_ENABLED: bool = True  # Master switch to toggle TMDB enrichment
    TMDB_AUTO_GROUP: bool = True  # Auto-reorganize enriched titles into '<LANG> | <GENRE>' groups
    TMDB_API_KEY: str = ""
    TMDB_RATE_LIMIT: float = 0.05  # Delay in seconds between TMDB API requests (20 req/s safe)

    # ── Database ──
    DATABASE_URL: str = "sqlite:///./data/pream.db"
    SQL_TIMING_ENABLED: bool = False  # Log slow SQL queries with execution time
    SQL_TIMING_THRESHOLD_MS: float = 10.0  # Threshold in ms to trigger slow query logging

    # ── Performance & Query Tuning ──
    OPT_DASHBOARD_SOURCE_COUNTS: bool = True  # Grouped source stream breakdown on dashboard
    OPT_DASHBOARD_TOTAL_STREAMS: bool = True  # Subtext counting total streams including hidden
    OPT_DASHBOARD_TMDB_PROGRESS: bool = True  # TMDB enrichment progress widget on dashboard
    OPT_LIBRARY_SOURCE_BADGES: bool = True  # Provider badges in library category sidebar
    OPT_LIBRARY_CATEGORY_COUNTS: bool = False  # Exact item counts in library category sidebar
    OPT_TASK_POLL_INTERVAL_MS: int = 1500  # Interval (ms) for footer status polling (1500, 3000, 5000, 10000, 0=off)
    OPT_RUN_STARTUP_TASKS: bool = False  # Automatically execute source refresh upon application startup

    # ── Scheduler ──
    REFRESH_CRON_ENABLED: bool = True  # Master switch for automated cron schedule
    REFRESH_CRON: str = "0 4 * * *"  # 4 AM daily

    # ── Global Source-Prefix Grouping ──
    GROUP_BY_SOURCE: bool = False  # Prefix all groups with source name

    # ── Source Scanning & Ingestion ──
    SOURCE_SCAN_THREADS: int = 1  # Concurrency for source ingestion (min: 1, max: 2)
    SOURCE_TIMEOUT: float = 60.0  # Timeout in seconds to abort slow or stuck source scans

    # ── Stream Health & Auto Re-prioritization ──
    STREAM_HEALTH: bool = True
    STREAM_HEALTH_COUNT: int = 4  # Max 5

    # ── Stream Failover & History (Zero-Bandwidth) ──
    STREAM_FAILOVER: bool = True  # Automatically 302-redirect to alternate healthy source if primary fails
    STREAM_HISTORY: bool = True  # Log playback history (IP, media, timestamp) with 30-day retention

    # ── Telegram Notifications ──
    TELEGRAM_ENABLED: bool = True  # Master switch for telegram notifications
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # ── Gemini AI Title Extraction ──
    GEMINI_ENABLED: bool = True  # Master switch for Gemini AI title & language extraction
    GEMINI_API_KEY: str = ""
    STAGGERED_GEMINI_CALLS: bool = True  # Stagger Gemini calls to stay safely within free/tier rate limits (15 RPM)
    GEMINI_RATE_DELAY: float = 4.0  # Seconds between staggered Gemini API calls (15 req/min = 4s)

    # ── Server ──
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    SERVER_NAME: str = "Pream"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
