"""Additive migration for the customer visit Status Master."""

from sqlalchemy.orm import Session

from models import CustomerVisit, CustomerVisitStatus


CORE_STATUSES = {
    "Draft", "Billed", "Walkout", "Old Gold", "Repair Taken",
    "Repair Delivered", "Ordered Delivered",
}


def migrate_009_visit_status_master(engine):
    """Create the master table and retain any custom statuses already in use."""
    CustomerVisitStatus.__table__.create(bind=engine, checkfirst=True)
    with Session(bind=engine) as db:
        existing = {
            name.casefold()
            for (name,) in db.query(CustomerVisitStatus.name).all()
        }
        used = set()
        for activity_type, status in db.query(
            CustomerVisit.activity_type, CustomerVisit.status
        ).distinct():
            displayed = status if activity_type == "New Jewellery" else activity_type
            if displayed and displayed not in CORE_STATUSES:
                used.add(displayed.strip())
        for name in sorted(used, key=str.casefold):
            if name and len(name) <= 40 and name.casefold() not in existing:
                db.add(CustomerVisitStatus(name=name))
                existing.add(name.casefold())
        db.commit()
