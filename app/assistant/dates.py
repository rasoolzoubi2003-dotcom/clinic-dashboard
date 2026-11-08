"""Turn the date words patients use into real dates, in code rather than in the model.

Small models are unreliable at date arithmetic ("Sunday" -> the wrong day), so the
find_available_slots tool accepts either YYYY-MM-DD or a word like "tomorrow" or
"Sunday" and resolves it here.
"""

import datetime as dt
import re

_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")

_WEEKDAYS = {
    0: ["monday", "mon"],
    1: ["tuesday", "tue", "tues"],
    2: ["wednesday", "wed"],
    3: ["thursday", "thu", "thurs"],
    4: ["friday", "fri"],
    5: ["saturday", "sat"],
    6: ["sunday", "sun"],
}
_RELATIVE = {
    0: ["today"],
    1: ["tomorrow"],
    2: ["day after tomorrow", "the day after tomorrow", "after tomorrow"],
}
_FILLER = re.compile(r"^(on|this|the)\s+")
_NEXT = re.compile(r"^next\s+")


def resolve_date(value: object, today: dt.date) -> object:
    """Return a date for recognised words; otherwise return the value unchanged for normal validation."""
    if isinstance(value, dt.date) or not isinstance(value, str):
        return value
    match = _ISO.search(value)
    if match:
        return match.group(0)

    text = " ".join(value.strip().casefold().split())
    text = _FILLER.sub("", text)
    for offset, words in _RELATIVE.items():
        if text in words:
            return today + dt.timedelta(days=offset)

    strictly_next = bool(_NEXT.search(text))
    text = _NEXT.sub("", text).strip()
    for weekday, words in _WEEKDAYS.items():
        if text in words:
            days_ahead = (weekday - today.weekday()) % 7
            if days_ahead == 0 and strictly_next:
                days_ahead = 7
            return today + dt.timedelta(days=days_ahead)
    return value
