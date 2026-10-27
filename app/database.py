"""
Database engine, session, and base model for SQLAlchemy ORM.
Uses SQLite with check_same_thread=False for APScheduler thread safety.
"""
import logging
import time

from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

logger = logging.getLogger("app.database.sql")

# SQL Query Timing Cache (updated at runtime when settings change)
_sql_timing_enabled: bool = settings.SQL_TIMING_ENABLED
_sql_timing_threshold_ms: float = settings.SQL_TIMING_THRESHOLD_MS


def update_sql_timing_settings(enabled: bool, threshold_ms: float = 10.0) -> None:
    """Dynamically update verbose SQL query logging configuration."""
    global _sql_timing_enabled, _sql_timing_threshold_ms
    _sql_timing_enabled = enabled
    _sql_timing_threshold_ms = threshold_ms


# SQLite-specific: allow multi-threaded access and robust busy timeout
connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False
    connect_args["timeout"] = 30.0

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,
    echo=False,
)

# Lightweight event hooks for timing SQL operations without measurable skew
@event.listens_for(engine, "before_cursor_execute")
def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    if _sql_timing_enabled:
        conn.info.setdefault("query_start_time", []).append(time.perf_counter())


@event.listens_for(engine, "after_cursor_execute")
def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    if _sql_timing_enabled:
        timers = conn.info.get("query_start_time")
        if timers:
            elapsed_ms = (time.perf_counter() - timers.pop()) * 1000.0
            if elapsed_ms >= _sql_timing_threshold_ms:
                # Truncate statement for neat log readability if very long
                stmt_clean = " ".join(statement.split())
                if len(stmt_clean) > 200:
                    stmt_clean = stmt_clean[:197] + "..."
                logger.warning(f"[SLOW SQL {elapsed_ms:.2f}ms] {stmt_clean}")


if settings.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        """Enable WAL mode and 30-second busy timeout for concurrent read/write support."""
        try:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()
        except Exception:
            pass

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """FastAPI dependency — yields a request-scoped database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _migrate_sqlite_schema(target_engine=None):
    """Ensure newly added columns exist in SQLite database tables without requiring manual migrations."""
    from sqlalchemy import inspect, text

    eng = target_engine or engine
    try:
        inspector = inspect(eng)
        table_names = inspector.get_table_names()

        with eng.begin() as conn:
            if "rules" in table_names:
                rules_cols = [c["name"] for c in inspector.get_columns("rules")]
                if "affected_count" not in rules_cols:
                    conn.execute(text("ALTER TABLE rules ADD COLUMN affected_count INTEGER NOT NULL DEFAULT 0"))

            if "sources" in table_names:
                source_cols = [c["name"] for c in inspector.get_columns("sources")]
                if "user_agent" not in source_cols:
                    conn.execute(text("ALTER TABLE sources ADD COLUMN user_agent TEXT"))
                if "expiry_date" not in source_cols:
                    conn.execute(text("ALTER TABLE sources ADD COLUMN expiry_date TEXT"))
                if "max_connections" not in source_cols:
                    conn.execute(text("ALTER TABLE sources ADD COLUMN max_connections TEXT"))
                if "selected_categories" not in source_cols:
                    conn.execute(text("ALTER TABLE sources ADD COLUMN selected_categories TEXT"))

            # Create performance indices for high-frequency search and grouping queries
            indices_to_create = [
                ("idx_channels_display_group", "channels", "display_group"),
                ("idx_channels_display_name", "channels", "display_name"),
                ("idx_channels_is_hidden", "channels", "is_hidden"),
                ("idx_movies_display_group", "movies", "display_group"),
                ("idx_movies_display_name", "movies", "display_name"),
                ("idx_movies_is_hidden", "movies", "is_hidden"),
                ("idx_movies_tmdb_fetched_at", "movies", "tmdb_fetched_at"),
                ("idx_series_display_group", "series", "display_group"),
                ("idx_series_display_name", "series", "display_name"),
                ("idx_series_is_hidden", "series", "is_hidden"),
                ("idx_series_tmdb_fetched_at", "series", "tmdb_fetched_at"),
                ("idx_channels_src_hidden", "channels", "source_id, is_hidden"),
                ("idx_movies_src_hidden", "movies", "source_id, is_hidden"),
                ("idx_series_src_hidden", "series", "source_id, is_hidden"),
                ("idx_movies_hidden_tmdb", "movies", "is_hidden, tmdb_id"),
                ("idx_series_hidden_tmdb", "series", "is_hidden, tmdb_id"),
            ]
            for idx_name, tbl, col in indices_to_create:
                if tbl in table_names:
                    try:
                        conn.execute(text(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {tbl} ({col})"))
                    except Exception:
                        pass
    except Exception:
        pass


def init_db(target_engine=None):
    """Create all tables if they don't exist and auto-migrate existing SQLite schema."""
    import app.models  # noqa: F401 — ensure all models are registered
    eng = target_engine or engine
    Base.metadata.create_all(bind=eng)
    _migrate_sqlite_schema(eng)

    # Initialize SQL timing configuration from persisted settings
    try:
        from app.models.setting import Setting
        with SessionLocal() as db:
            s_en = db.query(Setting).filter(Setting.key == "sql_timing_enabled").first()
            s_th = db.query(Setting).filter(Setting.key == "sql_timing_threshold_ms").first()
            en = (s_en.value.lower() == "true") if s_en else settings.SQL_TIMING_ENABLED
            th = float(s_th.value) if s_th else settings.SQL_TIMING_THRESHOLD_MS
            update_sql_timing_settings(en, th)
    except Exception:
        pass


# Run lightweight migration on import if SQLite database already exists
_migrate_sqlite_schema(engine)

