from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker
from settings import DATABASE_URL
if not DATABASE_URL.startswith('sqlite:///'):
    raise RuntimeError('This release supports a standalone SQLite database URL.')
engine = create_engine(DATABASE_URL, connect_args={'check_same_thread': False, 'timeout': 10})
@event.listens_for(engine, 'connect')
def configure_connection(connection, _):
    cursor = connection.cursor()
    cursor.execute('PRAGMA foreign_keys=ON')
    cursor.execute('PRAGMA journal_mode=WAL')
    cursor.execute('PRAGMA busy_timeout=10000')
    cursor.close()
Base = declarative_base()
SessionLocal = sessionmaker(bind=engine, autoflush=False)
def get_db():
    with SessionLocal() as session:
        yield session
def init_db():
    import models
    Base.metadata.create_all(engine)
    from visit_categories import migrate_003_categories_and_billed_lock
    migrate_003_categories_and_billed_lock(engine)
    from visit_options import migrate_001_visit_options
    migrate_001_visit_options(engine)
    from walkout_followups import migrate_002_walkout_followups
    migrate_002_walkout_followups(engine)
    from query_indexes import migrate_004_query_indexes
    migrate_004_query_indexes(engine)
    from store_scope import migrate_005_store_scope
    migrate_005_store_scope(engine)
    from visit_numbers import migrate_006_store_visit_numbers
    migrate_006_store_visit_numbers(engine)
    from product_selection import migrate_007_optional_product_selection
    migrate_007_optional_product_selection(engine)
    from feedback_schedule import migrate_008_feedback_schedule
    migrate_008_feedback_schedule(engine)
