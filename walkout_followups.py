"""Preserve walkout history and append-only follow-ups independently of final status."""
from sqlalchemy import text
from models import CreWalkoutRecord


def remember_walkout(db, visit):
    if visit.status == 'Walkout' and not visit.walkout_record:
        visit.walkout_record = CreWalkoutRecord(
            reason=visit.walkout_reason, competitor=visit.competitor_shop,
            remark=visit.remark,
        )
    elif visit.status == 'Walkout' and visit.walkout_record:
        # Keep the latest editable choices available after billing clears live fields.
        visit.walkout_record.reason = visit.walkout_reason
        visit.walkout_record.competitor = visit.competitor_shop


def migrate_002_walkout_followups(engine):
    with engine.begin() as connection:
        connection.execute(text('''
            INSERT OR IGNORE INTO cre_walkout_records
                (visit_id, reason, competitor, remark, recorded_at)
            SELECT id, walkout_reason, competitor_shop, remark, CURRENT_TIMESTAMP
            FROM customer_visits WHERE status = 'Walkout'
        '''))
        # Enforce the save-once rule even if a later route attempts an update.
        for operation in ('UPDATE', 'DELETE'):
            connection.exec_driver_sql(f'''
                CREATE TRIGGER IF NOT EXISTS cre_followup_no_{operation.lower()}
                BEFORE {operation} ON cre_walkout_followups
                BEGIN SELECT RAISE(ABORT, 'Saved follow-ups cannot be changed'); END
            ''')
