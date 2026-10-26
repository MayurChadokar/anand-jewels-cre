import hmac
import os

from fastapi import Header, HTTPException


def require_integration_key(x_integration_key: str = Header(default="")):
    expected_key = os.getenv("ANAND_INTEGRATION_KEY", "").strip()
    if not expected_key:
        raise HTTPException(status_code=503, detail="Integration is not configured")
    if not hmac.compare_digest(x_integration_key, expected_key):
        raise HTTPException(status_code=401, detail="Invalid integration key")
