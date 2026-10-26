from sqlalchemy import inspect


def migrate_005_store_scope(engine):
    inspector = inspect(engine)
    if 'cre_staff_members' not in inspector.get_table_names():
        return
    columns = {column['name'] for column in inspector.get_columns('cre_staff_members')}
    with engine.begin() as connection:
        if 'store_id' not in columns:
            connection.exec_driver_sql(
                'ALTER TABLE cre_staff_members ADD COLUMN store_id INTEGER REFERENCES stores(id)'
            )
        connection.exec_driver_sql(
            'CREATE INDEX IF NOT EXISTS ix_cre_staff_members_store_id ON cre_staff_members (store_id)'
        )
        connection.exec_driver_sql(
            'CREATE INDEX IF NOT EXISTS ix_admin_users_store_id ON admin_users (store_id)'
        )
