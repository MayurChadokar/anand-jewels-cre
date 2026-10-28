import argparse
import logging
import time

from database import SessionLocal, init_db
from services.feedback_worker import process_due_feedback
from settings import FEEDBACK_AUTO_SEND, FEEDBACK_WORKER_INTERVAL_SECONDS


def main():
    parser = argparse.ArgumentParser(description="Send due feedback WhatsApp messages")
    parser.add_argument("--once", action="store_true", help="Process one batch and exit")
    parser.add_argument("--batch-size", type=int, default=25)
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    init_db()

    while True:
        if FEEDBACK_AUTO_SEND:
            with SessionLocal() as db:
                count = process_due_feedback(db, batch_size=max(1, args.batch_size))
            if count:
                logging.info("Processed %s scheduled feedback record(s)", count)
        else:
            logging.info("Automatic feedback sending is disabled")
        if args.once:
            return
        time.sleep(FEEDBACK_WORKER_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
