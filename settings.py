import os
import secrets
from pathlib import Path
from dotenv import load_dotenv
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / '.env')
DATA_DIR = BASE_DIR / 'data'
DATA_DIR.mkdir(exist_ok=True)
DATABASE_URL = os.getenv('CRE_DATABASE_URL', 'sqlite:///' + (DATA_DIR / 'cre.db').as_posix())
TEMPLATE_DIR = BASE_DIR / 'templates'
STATIC_DIR = BASE_DIR / 'static'
COOKIE_NAME = os.getenv('CRE_SESSION_COOKIE', 'anand_cre_session')
COOKIE_SECURE = os.getenv('CRE_COOKIE_SECURE', 'false').lower() == 'true'
FEEDBACK_AUTO_SEND = os.getenv('FEEDBACK_AUTO_SEND', 'true').lower() == 'true'
FEEDBACK_SEND_DELAY_HOURS = max(0.0, float(os.getenv('FEEDBACK_SEND_DELAY_HOURS', '5')))
FEEDBACK_WORKER_INTERVAL_SECONDS = max(5, int(os.getenv('FEEDBACK_WORKER_INTERVAL_SECONDS', '30')))
FEEDBACK_MAX_ATTEMPTS = max(1, int(os.getenv('FEEDBACK_MAX_ATTEMPTS', '5')))
FEEDBACK_PROCESSING_TIMEOUT_MINUTES = max(1, int(os.getenv('FEEDBACK_PROCESSING_TIMEOUT_MINUTES', '15')))
def session_secret():
    value = os.getenv('CRE_SECRET_KEY')
    if value:
        return value
    path = DATA_DIR / 'session.key'
    try:
        with path.open('x', encoding='utf-8') as handle:
            handle.write(secrets.token_urlsafe(48))
    except FileExistsError:
        pass
    return path.read_text(encoding='utf-8').strip()
