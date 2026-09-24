"""Timezone convention for Moodle-scraped timestamps.

FLAME (lms.flame.edu.in) is an Indian institution; its Moodle renders due
dates in Moodle's configured site/user timezone, assumed here to be India
Standard Time (UTC+5:30) since FLAME is based in India. No timezone
identifier is exposed anywhere in the scraped HTML itself (see
BUILD_LOG.md's date-pipeline entry), so this is a reasoned assumption, not
a confirmed Moodle setting.

Root cause this module fixes: `moodle/sync_service.py` parsed a scraped
"Weekday, D Month YYYY H:MM" string with `datetime.strptime(...)`, which
produces a naive datetime carrying no timezone information at all. That
value — really IST — was then stored as-is and later compared against
`datetime.utcnow()`/`datetime.now()` elsewhere (assignment urgency
classification, the due-soon notification window) as if it were already
in the same timezone as "now". On a UTC-based server (e.g. Render) this
silently skewed every urgency/due-soon calculation by IST's fixed 5:30
offset — enough to misclassify an assignment across a due_today/
due_tomorrow/overdue boundary without ever showing up as a missing or
null due date, which is why the symptom looked like "urgency is
inconsistent" rather than "dates are absent".

Fix: every `Assignment.due_date` is stored as a naive datetime that
genuinely represents UTC (never IST, never local server time) — so
`datetime.utcnow()` is always the correct, timezone-consistent value to
compare it against, everywhere, regardless of what timezone the process
happens to run in. IST is reintroduced only at the display/reasoning
boundary, via `to_ist_aware()`/`to_ist_isoformat()` below.
"""

from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


def parse_moodle_datetime_to_utc(naive_moodle_time: datetime) -> datetime:
    """Takes a naive datetime as scraped/parsed from Moodle's rendered page
    text (assumed IST — see module docstring) and returns the equivalent
    naive UTC datetime, ready to store in Assignment.due_date."""
    aware_ist = naive_moodle_time.replace(tzinfo=IST)
    return aware_ist.astimezone(timezone.utc).replace(tzinfo=None)


def to_ist_aware(stored_utc: datetime | None) -> datetime | None:
    """Takes a naive-UTC datetime as stored in the database (see module
    docstring) and returns the equivalent timezone-AWARE datetime in IST —
    e.g. for a schemas.py response field, so it serializes with a real
    `+05:30` offset instead of an ambiguous naive string."""
    if stored_utc is None:
        return None
    aware_utc = stored_utc.replace(tzinfo=timezone.utc)
    return aware_utc.astimezone(IST)


def to_ist_isoformat(stored_utc: datetime | None) -> str | None:
    """Same as to_ist_aware(), but returns the ISO 8601 string directly —
    e.g. "2026-09-26T23:59:00+05:30" — for tool-call JSON payloads handed
    to Groq/Gemini, which need a plain string, not a datetime object."""
    aware = to_ist_aware(stored_utc)
    return aware.isoformat() if aware else None
