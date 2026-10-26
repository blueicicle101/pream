"""
Database engine, session, and base model for SQLAlchemy ORM.
Uses SQLite with check_same_thread=False for APScheduler thread safety.
"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

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
    except Exception:
        pass


def init_db(target_engine=None):
    """Create all tables if they don't exist and auto-migrate existing SQLite schema."""
    import app.models  # noqa: F401 — ensure all models are registered
    eng = target_engine or engine
    Base.metadata.create_all(bind=eng)
    _migrate_sqlite_schema(eng)


# Run lightweight migration on import if SQLite database already exists
_migrate_sqlite_schema(engine)

