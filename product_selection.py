NOT_SELECTED_PRODUCT = "Not Selected"


def migrate_007_optional_product_selection(engine):
    with engine.begin() as connection:
        tables = {
            row[0] for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "customer_product_types" not in tables:
            return
        connection.exec_driver_sql(
            "INSERT INTO customer_product_types (name, created_by, created_at) "
            "SELECT ?, NULL, CURRENT_TIMESTAMP "
            "WHERE NOT EXISTS ("
            "SELECT 1 FROM customer_product_types WHERE lower(name)=lower(?)"
            ")",
            (NOT_SELECTED_PRODUCT, NOT_SELECTED_PRODUCT),
        )
