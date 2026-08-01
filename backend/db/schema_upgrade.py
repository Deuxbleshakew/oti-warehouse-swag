"""Small additive schema upgrades for installs that already have a database.

SQLAlchemy's create_all creates new tables but does not add columns to existing
ones. These ALTER TABLE statements are intentionally additive and nullable so
old orders remain readable on SQLite and Postgres without a destructive reset.
"""
from sqlalchemy import inspect, text


PROJECT_SHIPPING_COLUMNS = {
    "shipping_address1": "VARCHAR(255) DEFAULT ''",
    "shipping_address2": "VARCHAR(255) DEFAULT ''",
    "shipping_city": "VARCHAR(120) DEFAULT ''",
    "shipping_state": "VARCHAR(2) DEFAULT ''",
    "shipping_postal_code": "VARCHAR(20) DEFAULT ''",
    "shipping_service": "VARCHAR(40) DEFAULT 'UPS Ground'",
    "ups_ground_days": "INTEGER",
    "ship_by_date": "VARCHAR(20) DEFAULT ''",
}


def ensure_additive_columns(engine) -> None:
    inspector = inspect(engine)
    if "projects" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("projects")}
    missing = [(name, ddl) for name, ddl in PROJECT_SHIPPING_COLUMNS.items()
               if name not in existing]
    if not missing:
        return
    with engine.begin() as connection:
        for name, ddl in missing:
            connection.execute(text(f"ALTER TABLE projects ADD COLUMN {name} {ddl}"))
