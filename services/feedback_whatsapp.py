import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


DEFAULT_KIT19_URL = "https://services.kit19.com/IMS/Whatsapp/Template"
DEFAULT_FEEDBACK_URL = "https://anandjewels.com/feedback.html"


def normalize_mobile(value: str) -> str:
    mobile = re.sub(r"\D", "", value or "")
    if len(mobile) == 10:
        return f"91{mobile}"
    if len(mobile) == 12 and mobile.startswith("91"):
        return mobile
    raise ValueError("Valid Indian WhatsApp number required")


def feedback_url(token: str) -> str:
    public_url = os.getenv("FEEDBACK_PUBLIC_URL", DEFAULT_FEEDBACK_URL).strip()
    parts = urlsplit(public_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["token"] = token
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def send_feedback_whatsapp(mobile_number: str, token: str) -> dict:
    kit19_url = os.getenv("KIT19_WHATSAPP_URL", DEFAULT_KIT19_URL).strip()
    kit19_key = os.getenv("KIT19_WHATSAPP_KEY", "").strip()
    kit19_username = os.getenv("KIT19_WHATSAPP_USERNAME", "").strip()
    if not kit19_key or not kit19_username:
        raise RuntimeError("Kit19 credentials are not configured")

    recipient = normalize_mobile(mobile_number)
    public_feedback_url = feedback_url(token)
    payload = {
        "key": kit19_key,
        "username": kit19_username,
        "name": "whatsapp",
        "remarks": (
            "Dear Customer,\n\n"
            "Your feedback ensures we continually improve. \n\n"
            "Please click below to share your thoughts on your recent visit at our store.\n"
            "*[@CustomField1@]*\n\n"
            "Thank you in advance!\n\n"
            "Regards,\n*Anand Jewels*"
        ),
        "whatsapp": {
            "to": recipient,
            "type": "template",
            "category": "UTILITY",
            "recipient_type": "individual",
            "template": {
                "namespace": "",
                "language": {"policy": "deterministic", "code": "en"},
                "name": "feedback_template",
                "components": [{
                    "type": "body",
                    "parameters": [{"type": "text", "text": public_feedback_url}],
                }],
            },
        },
    }
    request = Request(
        kit19_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Anand-CRE/1.0",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            body = response.read().decode("utf-8", errors="replace")
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Kit19 HTTP {error.code}: {body[:500]}") from error
    except URLError as error:
        raise RuntimeError(f"Unable to connect to Kit19: {error.reason}") from error

    try:
        result = json.loads(body)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Kit19 returned invalid JSON: {body[:500]}") from error
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    developer_message = (
        result.get("developerMessage")
        or result.get("developer_message")
        or meta.get("developerMessage")
        or meta.get("developer_message")
    )
    if result.get("success") is not True:
        raise RuntimeError(developer_message or result.get("message") or "Kit19 rejected request")
    request_id = (
        result.get("requestId")
        or result.get("request_id")
        or meta.get("requestId")
        or meta.get("request_id")
    )
    if not request_id and developer_message:
        match = re.search(r"request\s+id\s+([A-Za-z0-9_-]+)", developer_message, re.IGNORECASE)
        request_id = match.group(1) if match else ""
    return {
        "recipient": recipient,
        "feedback_url": public_feedback_url,
        "provider_response": body[:4000],
        "provider_request_id": str(request_id or ""),
    }
