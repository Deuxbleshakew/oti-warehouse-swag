"""Small additive schema upgrades for installs that already have a database.

SQLAlchemy's ``create_all`` creates new tables but does not add columns to
existing ones.  These ALTER TABLE statements are intentionally additive and
nullable or have safe defaults, so older SQLite and PostgreSQL installs can be
upgraded in place.

Keep the DDL dialect-aware.  SQLite accepts ``DATETIME`` and numeric boolean
defaults, while PostgreSQL requires a timestamp type and boolean literals.
"""
from sqlalchemy import inspect, text


def _table_columns_for_dialect(dialect_name: str) -> dict[str, dict[str, str]]:
    """Return portable column definitions for the active SQL dialect."""
    is_postgres = dialect_name == "postgresql"
    datetime_type = "TIMESTAMP WITHOUT TIME ZONE" if is_postgres else "DATETIME"
    false_literal = "FALSE" if is_postgres else "0"

    return {
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
            "picking_started_at": datetime_type,
            "fulfilled_at": datetime_type,
        },
        "order_lines": {
            "qty_estimated": (
                f"BOOLEAN NOT NULL DEFAULT {false_literal}"
            ),
        },
        "inventory_transactions": {
            "updated_at": datetime_type,
        },
    }


def ensure_additive_columns(engine) -> None:
    """Add missing v5.7 columns without rewriting or deleting existing data."""
    wanted_columns = _table_columns_for_dialect(engine.dialect.name)

    with engine.begin() as connection:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        quote = connection.dialect.identifier_preparer.quote

        for table_name, wanted in wanted_columns.items():
            if table_name not in tables:
                continue

            existing = {
                column["name"]
                for column in inspect(connection).get_columns(table_name)
            }
            for name, ddl in wanted.items():
                if name in existing:
                    continue
                connection.execute(text(
                    f"ALTER TABLE {quote(table_name)} "
                    f"ADD COLUMN {quote(name)} {ddl}"
                ))

        # Existing transaction rows predate updated_at. Populate them once so
        # response models can treat the value as non-null on both databases.
        if "inventory_transactions" in tables:
            cols = {
                column["name"]
                for column in inspect(connection).get_columns(
                    "inventory_transactions"
                )
            }
            if "updated_at" in cols:
                connection.execute(text(
                    "UPDATE inventory_transactions "
                    "SET updated_at = created_at WHERE updated_at IS NULL"
                ))
