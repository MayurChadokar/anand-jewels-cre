"""Add selectable categories and enforce final billing at the database boundary."""
def migrate_003_categories_and_billed_lock(engine):
    with engine.begin() as connection:
        columns = {row[1] for row in connection.exec_driver_sql('PRAGMA table_info(customer_visits)')}
        if 'category_id' not in columns:
            connection.exec_driver_sql('ALTER TABLE customer_visits ADD COLUMN category_id INTEGER REFERENCES customer_product_categories(id)')
        connection.exec_driver_sql('''
            CREATE TRIGGER IF NOT EXISTS cre_billed_no_update BEFORE UPDATE ON customer_visits
            WHEN OLD.status = 'Billed'
            BEGIN SELECT RAISE(ABORT, 'Billed visits are locked'); END
        ''')
        connection.exec_driver_sql('''
            CREATE TRIGGER IF NOT EXISTS cre_billed_no_delete BEFORE DELETE ON customer_visits
            WHEN OLD.status = 'Billed'
            BEGIN SELECT RAISE(ABORT, 'Billed visits are locked'); END
        ''')
        connection.exec_driver_sql('''
            CREATE TRIGGER IF NOT EXISTS cre_billed_no_followup BEFORE INSERT ON cre_walkout_followups
            WHEN (SELECT status FROM customer_visits WHERE id = NEW.visit_id) = 'Billed'
            BEGIN SELECT RAISE(ABORT, 'Billed visits are locked'); END
        ''')
