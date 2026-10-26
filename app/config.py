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

    # ── Telegram Notifications ──
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # ── Database ──
    DATABASE_URL: str = "sqlite:///./data/pream.db"

    # ── Scheduler ──
    REFRESH_CRON: str = "0 4 * * *"  # 4 AM daily

    # ── Global Source-Prefix Grouping ──
    GROUP_BY_SOURCE: bool = False  # Prefix all groups with source name

    # ── Source Scanning & Ingestion ──
    SOURCE_SCAN_THREADS: int = 2
    SOURCE_TIMEOUT: float = 60.0  # Timeout in seconds to abort slow or stuck source scans

    # ── Stream Health & Auto Re-prioritization ──
    STREAM_HEALTH: bool = True
    STREAM_HEALTH_COUNT: int = 4  # Max 5

    # ── Stream Failover & History (Zero-Bandwidth) ──
    STREAM_FAILOVER: bool = True  # Automatically 302-redirect to alternate healthy source if primary fails
    STREAM_HISTORY: bool = True  # Log playback history (IP, media, timestamp) with 30-day retention

    # ── Server ──
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    SERVER_NAME: str = "Pream"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
