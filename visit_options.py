"""Database-backed walkout choices and the additive 001 options migration."""
from sqlalchemy.orm import Session
from models import CreVisitOption, CustomerVisit

DEFAULT_REASONS = (
    "Budget", "Design", "Explore Market", "Idea / Visit", "Making Charges",
    "Other", "Payment Issue", "Rate Issue", "Size", "Stock Issue", "Thinking",
)
DEFAULT_COMPETITORS = (
    "Bluestone", "Caratlane", "DC", "DP Jewels", "Giri Jewels", "Indriya",
    "Jhandariya", "JMC", "Kalyan", "Katariya", "Local Sarafa", "Malabar",
    "Multani", "Nana Bhai", "Ora", "Punjabi Jewels", "Punjabi Saraf", "Ratlam",
    "Tanishq", "TBZ", "Other Shops",
)
OPTION_KINDS = {
    "reason": ("Walkout Reasons", 80, CustomerVisit.walkout_reason, DEFAULT_REASONS),
    "competitor": ("Competitor Shops", 100, CustomerVisit.competitor_shop, DEFAULT_COMPETITORS),
}


def migrate_001_visit_options(engine):
    # An immediate SQLite transaction also serializes concurrent server starts.
    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        with Session(bind=connection) as db:
            for kind, (_, limit, column, defaults) in OPTION_KINDS.items():
                if db.query(CreVisitOption.id).filter_by(kind=kind).first():
                    continue
                names = list(defaults) + [row[0] for row in db.query(column).distinct() if row[0]]
                seen = set()
                for name in names:
                    if name.lower() not in seen and len(name) <= limit:
                        db.add(CreVisitOption(kind=kind, name=name, is_active=1))
                        seen.add(name.lower())
            db.flush()
        connection.commit()


def option_names(db, kind, include_history=False):
    query = db.query(CreVisitOption).filter_by(kind=kind)
    if not include_history:
        query = query.filter_by(is_active=1)
    names = {row.name for row in query.all()}
    if include_history:
        column = OPTION_KINDS[kind][2]
        names.update(row[0] for row in db.query(column).distinct() if row[0])
    return sorted(names, key=str.casefold)


def valid_option(db, kind, value, previous=None):
    if value and value == previous:
        return True
    return bool(value and db.query(CreVisitOption.id).filter(
        CreVisitOption.kind == kind, CreVisitOption.is_active == 1,
        CreVisitOption.name == value,
    ).first())
