from datetime import datetime, timedelta

from fastapi import HTTPException
from models import FeedbackSendLog
from services.feedback_whatsapp import send_feedback_whatsapp


def send_feedback(db, feedback, requested_by, source, enforce_cooldown=True):
    if feedback.submitted_at:
        raise HTTPException(status_code=409, detail="Feedback is already submitted")
    if enforce_cooldown and feedback.sent_at and datetime.utcnow() - feedback.sent_at < timedelta(minutes=2):
        raise HTTPException(status_code=429, detail="Please wait before resending")

    try:
        result = send_feedback_whatsapp(feedback.customer.mobile_number, feedback.token)
        attempted_at = datetime.utcnow()
        feedback.sent_to = result["recipient"]
        feedback.send_status = "queued"
        feedback.provider_request_id = result["provider_request_id"] or None
        feedback.provider_response = result["provider_response"]
        feedback.sent_at = attempted_at
        feedback.scheduled_at = None
        feedback.next_attempt_at = None
        feedback.processing_started_at = None
        feedback.last_error = None
        feedback.updated_at = attempted_at
        feedback.send_attempts += 1
        db.add(FeedbackSendLog(
            feedback_id=feedback.id,
            requested_by=requested_by,
            source=source,
            status="queued",
            provider_request_id=result["provider_request_id"] or None,
            provider_response=result["provider_response"],
        ))
        db.commit()
        return result
    except Exception as error:
        attempted_at = datetime.utcnow()
        # A scheduled record remains eligible for the durable worker after a
        # failed manual attempt; unscheduled/manual-only records stay failed.
        feedback.send_status = "pending" if feedback.scheduled_at else "failed"
        feedback.provider_response = str(error)[:4000]
        feedback.last_error = str(error)[:4000]
        feedback.updated_at = attempted_at
        feedback.send_attempts += 1
        db.add(FeedbackSendLog(
            feedback_id=feedback.id,
            requested_by=requested_by,
            source=source,
            status="failed",
            provider_response=str(error)[:4000],
        ))
        db.commit()
        raise
