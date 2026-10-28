import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from database import Base
from feedback_schedule import migrate_008_feedback_schedule
from models import AdminUser, CustomerProductType, CustomerVisit, Store, WalkInFeedback
from services.feedback_worker import process_due_feedback, recover_stale_feedback


class FeedbackSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "scheduler.db"
        self.engine = create_engine("sqlite:///" + self.path.as_posix())
        Base.metadata.create_all(self.engine)
        self.addCleanup(self.engine.dispose)
        with Session(self.engine) as db:
            db.add(Store(id=1, store_name="Test", city="Indore", store_address="Address", timings="10-7"))
            db.add(AdminUser(id=1, username="owner", password_hash="unused", role="super_admin"))
            db.add(CustomerProductType(id=1, name="Ring", created_by=1))
            db.flush()
            db.add(CustomerVisit(
                id=1, customer_name="Scheduled customer", mobile_number="9876543210",
                product_type_id=1, store_id=1, activity_type="New Jewellery",
                status="Draft", created_by=1,
            ))
            db.commit()

    def add_feedback(self, scheduled_at, status="pending", attempts=0, processing_started_at=None):
        with Session(self.engine) as db:
            item = WalkInFeedback(
                customer_visit_id=1, token="scheduler-token", send_status=status,
                scheduled_at=scheduled_at, send_attempts=attempts,
                processing_started_at=processing_started_at,
            )
            db.add(item)
            db.commit()
            return item.id

    def test_worker_ignores_feedback_before_due_time(self):
        now = datetime(2026, 10, 28, 10)
        feedback_id = self.add_feedback(now + timedelta(seconds=1))
        with Session(self.engine) as db, patch("services.feedback_worker.send_feedback") as sender:
            self.assertEqual(process_due_feedback(db, now=now), 0)
            sender.assert_not_called()
            self.assertEqual(db.get(WalkInFeedback, feedback_id).send_status, "pending")

    def test_worker_claims_and_sends_due_feedback(self):
        now = datetime(2026, 10, 28, 10)
        feedback_id = self.add_feedback(now - timedelta(seconds=1))

        def successful_send(db, feedback, **_):
            feedback.send_status = "queued"
            feedback.sent_at = now
            feedback.scheduled_at = None
            feedback.processing_started_at = None
            feedback.send_attempts += 1
            db.commit()

        with Session(self.engine) as db, patch(
            "services.feedback_worker.send_feedback", side_effect=successful_send
        ) as sender:
            self.assertEqual(process_due_feedback(db, now=now), 1)
            sender.assert_called_once()
            saved = db.get(WalkInFeedback, feedback_id)
            self.assertEqual(saved.send_status, "queued")
            self.assertIsNone(saved.scheduled_at)

    def test_failure_is_retried_and_stale_claim_is_recovered(self):
        now = datetime(2026, 10, 28, 10)
        feedback_id = self.add_feedback(now - timedelta(seconds=1))

        def failed_send(db, feedback, **_):
            feedback.send_attempts += 1
            feedback.send_status = "failed"
            db.commit()
            raise RuntimeError("provider unavailable")

        with Session(self.engine) as db, patch(
            "services.feedback_worker.send_feedback", side_effect=failed_send
        ):
            self.assertEqual(process_due_feedback(db, now=now), 1)
            saved = db.get(WalkInFeedback, feedback_id)
            self.assertEqual(saved.send_status, "pending")
            self.assertEqual(saved.next_attempt_at, now + timedelta(minutes=5))

            saved.send_status = "processing"
            saved.processing_started_at = now - timedelta(hours=1)
            db.commit()
            self.assertEqual(recover_stale_feedback(db, now=now), 1)
            self.assertEqual(db.get(WalkInFeedback, feedback_id).send_status, "pending")

    def test_additive_migration_preserves_existing_rows(self):
        legacy_path = Path(self.tmp.name) / "legacy.db"
        legacy_engine = create_engine("sqlite:///" + legacy_path.as_posix())
        try:
            with legacy_engine.begin() as db:
                db.exec_driver_sql(
                    "CREATE TABLE walk_in_feedback (id INTEGER PRIMARY KEY, send_status TEXT)"
                )
                db.exec_driver_sql("INSERT INTO walk_in_feedback VALUES (1, 'pending')")
            migrate_008_feedback_schedule(legacy_engine)
            migrate_008_feedback_schedule(legacy_engine)
            with legacy_engine.connect() as db:
                columns = {row[1] for row in db.exec_driver_sql("PRAGMA table_info(walk_in_feedback)")}
                self.assertTrue({"scheduled_at", "next_attempt_at", "processing_started_at", "last_error"} <= columns)
                self.assertEqual(db.exec_driver_sql("SELECT id,send_status FROM walk_in_feedback").all(), [(1, "pending")])
        finally:
            legacy_engine.dispose()
