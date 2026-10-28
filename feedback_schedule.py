"""Add durable scheduling fields for delayed feedback WhatsApp delivery."""


def migrate_008_feedback_schedule(engine):
    with engine.begin() as connection:
        tables = {
            row[0] for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "walk_in_feedback" not in tables:
            return

        columns = {
            row[1] for row in connection.exec_driver_sql(
                "PRAGMA table_info(walk_in_feedback)"
            )
        }
        additions = (
            ("scheduled_at", "DATETIME"),
            ("next_attempt_at", "DATETIME"),
            ("processing_started_at", "DATETIME"),
            ("last_error", "TEXT"),
        )
        for name, column_type in additions:
            if name not in columns:
                connection.exec_driver_sql(
                    f"ALTER TABLE walk_in_feedback ADD COLUMN {name} {column_type}"
                )

        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_feedback_schedule_due "
            "ON walk_in_feedback (send_status, scheduled_at, next_attempt_at)"
        )
