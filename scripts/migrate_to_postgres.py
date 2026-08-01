"""
scripts/migrate_to_postgres.py — copies every row from the local SQLite
database to a Postgres database, preserving IDs so foreign keys still
line up. Run this ONCE when moving from local SQLite to Render (or any
other Postgres host) — without it, moving to Postgres means starting
over with an empty database.

Run this from YOUR OWN computer (not from inside Render) — Render's free
plan doesn't support Shell/SSH access, but that's fine, since this only
needs a normal network connection to the database. In the Render
dashboard, open your database, and copy the "External Database URL" (not
the internal one — that one only works from inside Render's own
network). Paste that as the argument below.

Usage:
    python scripts/migrate_to_postgres.py "postgresql://user:pass@host/db"

The target Postgres database must already exist with tables created —
Render's buildCommand runs scripts/init_db.py automatically on first
deploy (see render.yaml), so this is usually already done by the time
you run this.

What this does NOT copy: the sessions table. Those are login tokens tied
to whichever server issued them — everyone just logs in fresh on the new
deployment, which is expected and takes 5 seconds.

Safe to run against an already-migrated target: existing rows are
detected by primary key and skipped rather than duplicated, so re-running
after fixing something on the source is not destructive.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.session import Base, DATABASE_URL as SOURCE_URL
from backend.models.models import (
    Role, User, UserRole, Project, Item, ItemImage,
    InventoryTransaction, Order, OrderLine, Approval, AuditLog, AppSetting,
)

# Table order matters: each table must be inserted after everything it
# has a foreign key to, or the insert fails on a missing reference.
MODELS_IN_FK_ORDER = [
    Role, User, UserRole, Project, Item, ItemImage,
    InventoryTransaction, Order, OrderLine, Approval, AuditLog, AppSetting,
]


def _pk_columns(model):
    return [c.name for c in model.__table__.primary_key.columns]


def copy_table(model, source_session, target_session) -> int:
    pk_cols = _pk_columns(model)
    multi = len(pk_cols) > 1
    rows_in_target = target_session.query(
        *[getattr(model, c) for c in pk_cols]).all()
    existing = {tuple(r) if multi else tuple(r)[0] for r in rows_in_target}

    rows = source_session.query(model).all()
    copied = 0
    for row in rows:
        vals = tuple(getattr(row, c) for c in pk_cols)
        key = vals if multi else vals[0]
        if key in existing:
            continue   # already migrated in a prior run
        cols = {c.name: getattr(row, c.name) for c in model.__table__.columns}
        target_session.add(model(**cols))
        copied += 1
    target_session.commit()
    return copied


def fix_sequence(target_engine, table_name: str):
    """After inserting explicit IDs, Postgres's auto-increment sequence
    doesn't know about them — the next plain insert would collide with
    an existing row. This bumps the sequence to start past the highest
    ID actually in the table. No-op for tables with no rows."""
    with target_engine.connect() as conn:
        conn.exec_driver_sql(
            f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {table_name}), 1), "
            f"(SELECT COUNT(*) FROM {table_name}) > 0)")
        conn.commit()


def main():
    if len(sys.argv) != 2:
        print("Usage: python scripts/migrate_to_postgres.py "
              "\"postgresql://user:pass@host/dbname\"")
        sys.exit(1)
    target_url = sys.argv[1]
    if target_url.startswith("postgres://"):
        target_url = "postgresql://" + target_url[len("postgres://"):]

    if SOURCE_URL == target_url:
        print("Source and target are the same database — nothing to do.")
        sys.exit(1)

    print(f"Source: {SOURCE_URL}")
    print(f"Target: {target_url}")
    confirm = input("Copy all data from source to target? [y/N] ")
    if confirm.strip().lower() != "y":
        print("Cancelled.")
        sys.exit(0)

    source_engine = create_engine(SOURCE_URL, future=True)
    target_engine = create_engine(target_url, future=True)
    Base.metadata.create_all(target_engine)   # no-op if already migrated

    SourceSession = sessionmaker(bind=source_engine, future=True)
    TargetSession = sessionmaker(bind=target_engine, future=True)
    src, tgt = SourceSession(), TargetSession()

    try:
        for model in MODELS_IN_FK_ORDER:
            n = copy_table(model, src, tgt)
            print(f"  {model.__tablename__}: {n} row(s) copied")
        for model in MODELS_IN_FK_ORDER:
            if _pk_columns(model) == ["id"]:
                fix_sequence(target_engine, model.__tablename__)
        print("Migration complete. Everyone logs in fresh on the new "
              "deployment — session tokens weren't carried over.")
    finally:
        src.close()
        tgt.close()


if __name__ == "__main__":
    main()
