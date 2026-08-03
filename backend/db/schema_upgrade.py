"""Small additive schema upgrades for existing SQLite and PostgreSQL installs.

SQLAlchemy ``create_all`` creates new tables but does not add columns to
existing tables. These upgrades are additive and safe to rerun. They also
backfill immutable item snapshots so deleting an item can release its part
number without erasing historical labels.
"""
from sqlalchemy import inspect, text


def _table_columns_for_dialect(dialect_name: str) -> dict[str, dict[str, str]]:
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
            "address_mode": "VARCHAR(20) NOT NULL DEFAULT 'variable'",
            "active": f"BOOLEAN NOT NULL DEFAULT {'TRUE' if is_postgres else '1'}",
            "access_restricted": f"BOOLEAN NOT NULL DEFAULT {false_literal}",
            "deleted_at": datetime_type,
            "deleted_name": "VARCHAR(200)",
        },
        "users": {
            "deleted_at": datetime_type,
            "theme": "VARCHAR(30) NOT NULL DEFAULT 'warehouse-dark'",
            "catalog_access_mode": "VARCHAR(20) NOT NULL DEFAULT 'all'",
        },
        "items": {
            "deleted_at": datetime_type,
            "deleted_code": "VARCHAR(60)",
            "deleted_name": "VARCHAR(200)",
            "inventory_counted": f"BOOLEAN NOT NULL DEFAULT {'TRUE' if is_postgres else '1'}",
            "nav_tracked": f"BOOLEAN NOT NULL DEFAULT {false_literal}",
            "nav_item_number": "VARCHAR(80) DEFAULT ''",
        },
        "orders": {
            "picking_started_at": datetime_type,
            "fulfilled_at": datetime_type,
            "deleted_at": datetime_type,
        },
        "order_lines": {
            "qty_estimated": f"BOOLEAN NOT NULL DEFAULT {false_literal}",
            "item_code_snapshot": "VARCHAR(60) DEFAULT ''",
            "item_name_snapshot": "VARCHAR(200) DEFAULT ''",
            "item_location_snapshot": "VARCHAR(120) DEFAULT ''",
        },
        "inventory_transactions": {
            "updated_at": datetime_type,
            "item_code_snapshot": "VARCHAR(60) DEFAULT ''",
            "item_name_snapshot": "VARCHAR(200) DEFAULT ''",
        },
        "count_requests": {
            "system_qty_before": "INTEGER",
            "physical_qty": "INTEGER",
            "adjustment_delta": "INTEGER",
        },
    }


def _column_names(connection, table_name: str) -> set[str]:
    return {column["name"] for column in inspect(connection).get_columns(table_name)}


def _backfill_item_snapshots(connection, tables: set[str]) -> None:
    if "items" not in tables:
        return

    item_rows = connection.execute(text(
        "SELECT id, code, name, location, deleted_at, deleted_code, deleted_name "
        "FROM items"
    )).mappings().all()
    items = {int(row["id"]): row for row in item_rows}

    if "order_lines" in tables:
        cols = _column_names(connection, "order_lines")
        needed = {"item_code_snapshot", "item_name_snapshot", "item_location_snapshot"}
        if needed.issubset(cols):
            rows = connection.execute(text(
                "SELECT id, item_id, item_code_snapshot, item_name_snapshot, "
                "item_location_snapshot FROM order_lines"
            )).mappings().all()
            for row in rows:
                item = items.get(int(row["item_id"]))
                if not item:
                    continue
                code = item["deleted_code"] or item["code"] or ""
                name = item["deleted_name"] or item["name"] or "Deleted item"
                location = item["location"] or ""
                if row["item_code_snapshot"] and row["item_name_snapshot"]:
                    continue
                connection.execute(text(
                    "UPDATE order_lines SET item_code_snapshot=:code, "
                    "item_name_snapshot=:name, item_location_snapshot=:location "
                    "WHERE id=:id"
                ), {"code": code, "name": name, "location": location,
                    "id": row["id"]})

    if "inventory_transactions" in tables:
        cols = _column_names(connection, "inventory_transactions")
        needed = {"item_code_snapshot", "item_name_snapshot"}
        if needed.issubset(cols):
            rows = connection.execute(text(
                "SELECT id, item_id, item_code_snapshot, item_name_snapshot "
                "FROM inventory_transactions"
            )).mappings().all()
            for row in rows:
                item = items.get(int(row["item_id"]))
                if not item:
                    continue
                if row["item_code_snapshot"] and row["item_name_snapshot"]:
                    continue
                connection.execute(text(
                    "UPDATE inventory_transactions SET item_code_snapshot=:code, "
                    "item_name_snapshot=:name WHERE id=:id"
                ), {"code": item["deleted_code"] or item["code"] or "",
                    "name": item["deleted_name"] or item["name"] or "Deleted item",
                    "id": row["id"]})


def _release_legacy_deleted_codes(connection, tables: set[str]) -> None:
    """Older builds kept the original unique code on soft-deleted items.

    Move that code/name into deletion metadata and give the tombstone a unique
    internal code. The original part number then becomes available for reuse.
    """
    if "items" not in tables:
        return
    cols = _column_names(connection, "items")
    if not {"deleted_at", "deleted_code", "deleted_name"}.issubset(cols):
        return
    rows = connection.execute(text(
        "SELECT id, code, name, deleted_code, deleted_name FROM items "
        "WHERE deleted_at IS NOT NULL"
    )).mappings().all()
    for row in rows:
        original_code = (row["deleted_code"] or row["code"] or "").strip()
        raw_name = (row["deleted_name"] or row["name"] or "Deleted item").strip()
        original_name = raw_name.replace(" [Deleted Item]", "").strip()
        tombstone_code = f"DELETED-{row['id']}"
        tombstone_name = f"{original_name} [Deleted Item]"[:200]
        connection.execute(text(
            "UPDATE items SET code=:code, name=:name, deleted_code=:deleted_code, "
            "deleted_name=:deleted_name WHERE id=:id"
        ), {"code": tombstone_code[:60], "name": tombstone_name,
            "deleted_code": original_code[:60], "deleted_name": original_name[:200],
            "id": row["id"]})


def ensure_additive_columns(engine) -> None:
    wanted_columns = _table_columns_for_dialect(engine.dialect.name)

    with engine.begin() as connection:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        quote = connection.dialect.identifier_preparer.quote

        for table_name, wanted in wanted_columns.items():
            if table_name not in tables:
                continue
            existing = _column_names(connection, table_name)
            for name, ddl in wanted.items():
                if name in existing:
                    continue
                connection.execute(text(
                    f"ALTER TABLE {quote(table_name)} "
                    f"ADD COLUMN {quote(name)} {ddl}"
                ))

        if "inventory_transactions" in tables:
            cols = _column_names(connection, "inventory_transactions")
            if "updated_at" in cols:
                connection.execute(text(
                    "UPDATE inventory_transactions SET updated_at = created_at "
                    "WHERE updated_at IS NULL"
                ))

        _backfill_item_snapshots(connection, tables)
        _release_legacy_deleted_codes(connection, tables)
