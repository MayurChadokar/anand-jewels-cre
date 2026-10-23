"""Display database UTC timestamps consistently in Indian Standard Time."""
from datetime import timedelta, timezone

INDIA_TIME = timezone(timedelta(hours=5, minutes=30))


def format_ist(value, pattern='%d %b %Y, %I:%M %p IST'):
    if value is None:
        return '-'
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(INDIA_TIME).strftime(pattern)
