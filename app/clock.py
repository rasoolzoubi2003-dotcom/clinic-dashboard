"""Current date/time in the clinic's timezone (servers and containers usually run in UTC)."""

import datetime as dt
from zoneinfo import ZoneInfo

from app.config import CLINIC_TIMEZONE

_TZ = ZoneInfo(CLINIC_TIMEZONE)


def now() -> dt.datetime:
    """Naive local datetime in the clinic's timezone, comparable with stored slot times."""
    return dt.datetime.now(_TZ).replace(tzinfo=None)


def today() -> dt.date:
    return now().date()
