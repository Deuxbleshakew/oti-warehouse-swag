"""
backend/db/session.py — Engine + session factory.

The whole point of using SQLAlchemy here (instead of raw sqlite3 calls) is
this file: swapping SQLite for Postgres later means changing DATABASE_URL
and nothing else. Every model, every query, every service function stays
identical because SQLAlchemy abstracts the dialect.
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# --- Where the database lives -----------------------------------------------
# SQLite for v1. This file must sit on the SAME trusted machine that runs
# the backend service — never in a shared/synced folder. To move to Postgres
# later: set DATABASE_URL to e.g. "postgresql://user:pass@host/dbname" and
# nothing else in this codebase needs to change.
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(os.path.dirname(APP_DIR), "data")
os.makedirs(DATA_DIR, exist_ok=True)
DEFAULT_SQLITE_PATH = os.path.join(DATA_DIR, "swag_system.db")

DATABASE_URL = (
    os.environ.get("DATABASE_URL")            # Render's own Postgres add-on
    or os.environ.get("SWAG_DATABASE_URL")    # manual override (any host)
    or f"sqlite:///{DEFAULT_SQLITE_PATH}"     # local dev default
)
# Render (and most managed Postgres providers) hand out URLs starting with
# "postgres://", a scheme SQLAlchemy 1.4+ no longer accepts — it wants the
# explicit "postgresql://". Rewriting it here means the connection string
# from Render's dashboard can be pasted in as-is with no manual editing.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgres://"):]

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    # SQLite needs this because FastAPI can hand a request to a different
    # thread than the one that opened the connection. Safe here because we
    # only ever open ONE connection at a time per request via get_db().
    connect_args = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine,
                             future=True)
Base = declarative_base()


def get_db():
    """FastAPI dependency — yields one session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def enable_sqlite_foreign_keys():
    """SQLite ignores FOREIGN KEY constraints unless told otherwise per
    connection. Postgres enforces them natively, so this is a no-op there."""
    if DATABASE_URL.startswith("sqlite"):
        from sqlalchemy import event

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()


enable_sqlite_foreign_keys()
