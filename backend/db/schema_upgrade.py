"""Small additive schema upgrades for installs that already have a database.

SQLAlchemy's create_all creates new tables but does not add columns to existing
ones. These ALTER TABLE statements are intentionally additive and nullable or
have safe defaults, so older SQLite/Postgres installs can be upgraded in place.
"""
from sqlalchemy import inspect, text


TABLE_COLUMNS = {
    "projects": {
        "shipping_address1": "VARCHAR(255) DEFAULT ''",
        "shipping_address2": "VARCHAR(255) DEFAULT ''",
        "shipping_city": "VARCHAR(120) DEFAULT ''",
        "shipping_state": "VARCHAR(2) DEFAULT ''",
        "shipping_postal_code": "VARCHAR(20) DEFAULT ''",
        "shipping_service": "VARCHAR(40) DEFAULT 'UPS Ground'",
        "ups_ground_days": "INTEGER",
        "ship_by_date": "VARCHAR(20) DEFAULT ''",
    },
    "orders": {
        "picking_started_at": "DATETIME",
        "fulfilled_at": "DATETIME",
    },
    "order_lines": {
        "qty_estimated": "BOOLEAN NOT NULL DEFAULT 0",
    },
    "inventory_transactions": {
        "updated_at": "DATETIME",
    },
}


def ensure_additive_columns(engine) -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    with engine.begin() as connection:
        for table_name, wanted in TABLE_COLUMNS.items():
            if table_name not in tables:
                continue
            existing = {column["name"] for column in inspector.get_columns(table_name)}
            for name, ddl in wanted.items():
                if name not in existing:
                    connection.execute(text(
                        f"ALTER TABLE {table_name} ADD COLUMN {name} {ddl}"))

        # Existing transaction rows predate updated_at. Populate them once so
        # response models can treat the value as non-null on both databases.
        if "inventory_transactions" in tables:
            cols = {column["name"] for column in inspect(engine).get_columns(
                "inventory_transactions")}
            if "updated_at" in cols:
                connection.execute(text(
                    "UPDATE inventory_transactions "
                    "SET updated_at = created_at WHERE updated_at IS NULL"))
