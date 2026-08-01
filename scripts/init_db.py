"""
scripts/init_db.py — creates the database and all tables from the models.

Run this once before starting the backend for the first time:
    python scripts/init_db.py

Safe to run again later — create_all() only creates tables that don't
already exist; it never drops or alters existing ones. For real schema
changes down the line, use Alembic migrations instead of re-running this.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.db.session import engine, Base, DATABASE_URL
from backend.db.schema_upgrade import ensure_additive_columns
from backend.models import models  # noqa: F401 — import registers all tables


def main():
    print(f"Creating tables at: {DATABASE_URL}")
    Base.metadata.create_all(bind=engine)
    ensure_additive_columns(engine)
    table_names = sorted(Base.metadata.tables.keys())
    print(f"Created/verified {len(table_names)} tables:")
    for t in table_names:
        print(f"  - {t}")


if __name__ == "__main__":
    main()
