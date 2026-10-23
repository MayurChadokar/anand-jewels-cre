"""Add indexes for filtered visit lists without rewriting business data."""


def migrate_004_query_indexes(engine):
    with engine.begin() as connection:
        for name, columns in (
            ('ix_cre_visits_activity_status_date', 'activity_type, status, created_at'),
            ('ix_cre_visits_sales_date', 'handled_by_id, created_at'),
            ('ix_cre_visits_manager_date', 'assigned_manager_id, created_at'),
            ('ix_cre_visits_store_date', 'store_id, created_at'),
        ):
            connection.exec_driver_sql(
                f'CREATE INDEX IF NOT EXISTS {name} ON customer_visits ({columns})'
            )
