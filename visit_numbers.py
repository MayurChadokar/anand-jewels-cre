from sqlalchemy import inspect


KNOWN_PREFIXES = (
    ("mg road", "IND"),
    ("indore", "IND"),
    ("pandri", "RAI"),
    ("raipur", "RAI"),
    ("malviya nagar", "BMN"),
    ("db mall", "BDP"),
)


def _preferred_prefix(store_id, store_name, city, used):
    searchable = f"{store_name or ''} {city or ''}".lower()
    for text, prefix in KNOWN_PREFIXES:
        if text in searchable and prefix not in used:
            return prefix
    base = f"S{store_id}"
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}{suffix}"
        suffix += 1
    return candidate


def migrate_006_store_visit_numbers(engine):
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if not {"stores", "customer_visits"}.issubset(tables):
        return
    store_columns = {column["name"] for column in inspector.get_columns("stores")}
    visit_columns = {column["name"] for column in inspector.get_columns("customer_visits")}
    with engine.begin() as connection:
        if "visit_prefix" not in store_columns:
            connection.exec_driver_sql("ALTER TABLE stores ADD COLUMN visit_prefix VARCHAR(8)")
        if "next_visit_number" not in store_columns:
            connection.exec_driver_sql("ALTER TABLE stores ADD COLUMN next_visit_number INTEGER")
        if "store_visit_number" not in visit_columns:
            connection.exec_driver_sql("ALTER TABLE customer_visits ADD COLUMN store_visit_number INTEGER")

        stores = connection.exec_driver_sql(
            "SELECT id, store_name, city, visit_prefix FROM stores ORDER BY id"
        ).all()
        used = {row[3].upper() for row in stores if row[3]}
        for store_id, store_name, city, current_prefix in stores:
            prefix = _preferred_prefix(store_id, store_name, city, used - ({current_prefix.upper()} if current_prefix else set()))
            if current_prefix and current_prefix.upper() not in {"IND", "RAI", "BMN", "BDP"}:
                prefix = current_prefix.upper()
            connection.exec_driver_sql(
                "UPDATE stores SET visit_prefix=? WHERE id=?", (prefix, store_id)
            )
            used.add(prefix)

        trigger = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='cre_billed_no_update'"
        ).first()
        if trigger:
            connection.exec_driver_sql("DROP TRIGGER cre_billed_no_update")
        for store_id, in connection.exec_driver_sql("SELECT id FROM stores ORDER BY id"):
            existing = connection.exec_driver_sql(
                "SELECT COALESCE(MAX(store_visit_number), 0) FROM customer_visits WHERE store_id=?",
                (store_id,),
            ).scalar_one()
            missing = connection.exec_driver_sql(
                "SELECT id FROM customer_visits WHERE store_id=? AND store_visit_number IS NULL "
                "ORDER BY created_at, id",
                (store_id,),
            ).all()
            for offset, (visit_id,) in enumerate(missing, 1):
                connection.exec_driver_sql(
                    "UPDATE customer_visits SET store_visit_number=? WHERE id=?",
                    (existing + offset, visit_id),
                )
            next_number = existing + len(missing) + 1
            connection.exec_driver_sql(
                "UPDATE stores SET next_visit_number=? WHERE id=?",
                (next_number, store_id),
            )
        if trigger:
            connection.exec_driver_sql(trigger[0])

        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_stores_visit_prefix "
            "ON stores (visit_prefix) WHERE visit_prefix IS NOT NULL"
        )
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_customer_visits_store_number "
            "ON customer_visits (store_id, store_visit_number) "
            "WHERE store_id IS NOT NULL AND store_visit_number IS NOT NULL"
        )
