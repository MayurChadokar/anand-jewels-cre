"""Additive migration preventing repeat mobile-number entries within 24 hours."""

import re

from sqlalchemy import inspect, text


def normalize_mobile(value):
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 12 and digits.startswith("91"):
        return digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        return digits[1:]
    return digits


def migrate_010_customer_deduplication(engine):
    with engine.begin() as connection:
        columns = {column["name"] for column in inspect(connection).get_columns("customer_visits")}
        if "mobile_number_normalized" not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE customer_visits ADD COLUMN mobile_number_normalized VARCHAR(15)"
            )
        # Migration 003 intentionally blocks every update to billed visits. Temporarily
        # remove that trigger inside this transaction so the one-time derived-value
        # backfill can include historical billed rows, then restore the same lock.
        connection.exec_driver_sql("DROP TRIGGER IF EXISTS cre_billed_no_update")
        rows = connection.execute(text(
            "SELECT id, mobile_number FROM customer_visits "
            "WHERE mobile_number_normalized IS NULL OR mobile_number_normalized = ''"
        )).all()
        for visit_id, mobile_number in rows:
            connection.execute(text(
                "UPDATE customer_visits SET mobile_number_normalized=:mobile WHERE id=:visit_id"
            ), {"mobile": normalize_mobile(mobile_number), "visit_id": visit_id})
        connection.exec_driver_sql("""
            CREATE TRIGGER cre_billed_no_update BEFORE UPDATE ON customer_visits
            WHEN OLD.status = 'Billed'
            BEGIN SELECT RAISE(ABORT, 'Billed visits are locked'); END
        """)
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_customer_visits_mobile_number_normalized "
            "ON customer_visits (mobile_number_normalized)"
        )
        connection.exec_driver_sql("DROP TRIGGER IF EXISTS prevent_duplicate_customer_mobile_24h")
        connection.exec_driver_sql("""
            CREATE TRIGGER prevent_duplicate_customer_mobile_24h
            BEFORE INSERT ON customer_visits
            WHEN NEW.mobile_number_normalized IS NOT NULL
              AND NEW.mobile_number_normalized != ''
              AND EXISTS (
                SELECT 1 FROM customer_visits
                WHERE mobile_number_normalized = NEW.mobile_number_normalized
                  AND created_at >= datetime('now', '-24 hours')
              )
            BEGIN
              SELECT RAISE(ABORT, 'duplicate_mobile_24h');
            END
        """)
