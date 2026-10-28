import logging
from datetime import datetime, timedelta

from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from models import WalkInFeedback
from services.feedback import send_feedback
from settings import FEEDBACK_MAX_ATTEMPTS, FEEDBACK_PROCESSING_TIMEOUT_MINUTES


LOGGER = logging.getLogger(__name__)
RETRY_DELAYS_MINUTES = (5, 15, 30, 60)


def _retry_delay(attempts):
    index = min(max(attempts - 1, 0), len(RETRY_DELAYS_MINUTES) - 1)
    return timedelta(minutes=RETRY_DELAYS_MINUTES[index])


def recover_stale_feedback(db, now=None):
    now = now or datetime.utcnow()
    cutoff = now - timedelta(minutes=FEEDBACK_PROCESSING_TIMEOUT_MINUTES)
    count = db.query(WalkInFeedback).filter(
        WalkInFeedback.send_status == "processing",
        WalkInFeedback.processing_started_at < cutoff,
        WalkInFeedback.submitted_at.is_(None),
    ).update({
        WalkInFeedback.send_status: "pending",
        WalkInFeedback.processing_started_at: None,
        WalkInFeedback.next_attempt_at: now,
        WalkInFeedback.updated_at: now,
    }, synchronize_session=False)
    db.commit()
    return count


def _due_feedback_ids(db, now, batch_size):
    return [row[0] for row in db.query(WalkInFeedback.id).filter(
        WalkInFeedback.send_status == "pending",
        WalkInFeedback.submitted_at.is_(None),
        WalkInFeedback.scheduled_at.is_not(None),
        WalkInFeedback.scheduled_at <= now,
        or_(
            WalkInFeedback.next_attempt_at.is_(None),
            WalkInFeedback.next_attempt_at <= now,
        ),
    ).order_by(
        WalkInFeedback.scheduled_at.asc(), WalkInFeedback.id.asc()
    ).limit(batch_size).all()]


def process_due_feedback(db, now=None, batch_size=25):
    now = now or datetime.utcnow()
    recover_stale_feedback(db, now)
    processed = 0

    for feedback_id in _due_feedback_ids(db, now, batch_size):
        claimed = db.query(WalkInFeedback).filter(
            WalkInFeedback.id == feedback_id,
            WalkInFeedback.send_status == "pending",
            WalkInFeedback.submitted_at.is_(None),
            WalkInFeedback.scheduled_at <= now,
            or_(
                WalkInFeedback.next_attempt_at.is_(None),
                WalkInFeedback.next_attempt_at <= now,
            ),
        ).update({
            WalkInFeedback.send_status: "processing",
            WalkInFeedback.processing_started_at: now,
            WalkInFeedback.updated_at: now,
        }, synchronize_session=False)
        db.commit()
        if not claimed:
            continue

        feedback = db.query(WalkInFeedback).options(
            joinedload(WalkInFeedback.customer)
        ).filter(WalkInFeedback.id == feedback_id).first()
        if not feedback or feedback.submitted_at:
            continue

        try:
            send_feedback(
                db, feedback, requested_by=None,
                source="automatic_scheduled", enforce_cooldown=False,
            )
            LOGGER.info("Queued scheduled feedback %s", feedback_id)
        except Exception as error:
            db.expire_all()
            failed = db.get(WalkInFeedback, feedback_id)
            if failed and failed.send_attempts < FEEDBACK_MAX_ATTEMPTS:
                failed.send_status = "pending"
                failed.next_attempt_at = now + _retry_delay(failed.send_attempts)
            elif failed:
                failed.send_status = "failed"
                failed.next_attempt_at = None
            if failed:
                failed.processing_started_at = None
                failed.last_error = str(error)[:4000]
                failed.updated_at = now
                db.commit()
            LOGGER.exception("Scheduled feedback %s failed", feedback_id)
        processed += 1

    return processed
