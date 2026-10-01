"""Value normalisation used by validation: text, lists, times, timezones and channels."""

import re
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

WHITESPACE = re.compile(r"\s+")
LIST_SPLIT = re.compile(r"\s*(?:,|;|\band\b|&)\s*", re.IGNORECASE)
EMAIL = re.compile(r"[^@\s,;<>()]+@[^@\s,;<>()]+\.[a-z]{2,}", re.IGNORECASE)
SLACK_CHANNEL = re.compile(r"[a-z0-9][a-z0-9_-]{0,79}")
TIME = re.compile(r"(\d{1,2})(?::|\.)?(\d{2})?\s*(am|pm|a\.m\.|p\.m\.)?")
TZ_OFFSET = re.compile(r"(?:utc|gmt)?\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?")
VAGUE_TIMES: dict[str, tuple[str, ...]] = {
    "morning": ("8:00 AM", "9:00 AM", "10:00 AM"),
    "afternoon": ("1:00 PM", "2:00 PM", "3:00 PM"),
    "evening": ("5:00 PM", "6:00 PM", "7:00 PM"),
    "night": ("8:00 PM", "9:00 PM", "10:00 PM"),
    "end of day": ("5:00 PM", "6:00 PM"),
    "eod": ("5:00 PM", "6:00 PM"),
}
TZ_ALIASES = {
    "ist": "Asia/Kolkata", "india": "Asia/Kolkata", "indian time": "Asia/Kolkata", "delhi": "Asia/Kolkata",
    "mumbai": "Asia/Kolkata", "bangalore": "Asia/Kolkata", "bengaluru": "Asia/Kolkata", "utc": "UTC", "gmt": "GMT",
    "est": "America/New_York", "edt": "America/New_York", "eastern": "America/New_York",
    "cst": "America/Chicago", "central": "America/Chicago", "mst": "America/Denver",
    "pst": "America/Los_Angeles", "pdt": "America/Los_Angeles", "pacific": "America/Los_Angeles",
    "cet": "Europe/Paris", "bst": "Europe/London", "uk": "Europe/London", "aest": "Australia/Sydney",
    "jst": "Asia/Tokyo", "sgt": "Asia/Singapore", "gst": "Asia/Dubai", "dubai": "Asia/Dubai",
}
VAGUE_TIMEZONES = frozenset({"my timezone", "my time zone", "local", "local time", "my time", "our timezone", "here"})


def normalize_text(text: str) -> str:
    """Trim, collapse whitespace, drop wrapping quotes and trailing full stops."""
    text = WHITESPACE.sub(" ", text).strip().strip("\"'`“”‘’").strip()
    return re.sub(r"[.!]+$", "", text).strip()


def plain(text: str) -> str:
    return WHITESPACE.sub(" ", re.sub(r"[^\w\s#@+:/&-]", " ", text.lower())).strip()


def split_list(text: str) -> list[str]:
    return [item.strip() for item in LIST_SPLIT.split(text) if item.strip()]


def parse_time(text: str) -> tuple[str | None, list[str]]:
    """("HH:MM", []) for a clear time, or (None, interpretations) when it is unclear."""
    lowered = re.sub(r"^(?:at|around|by)\s+", "", plain(text).replace("o'clock", "").strip())
    if lowered in ("noon", "midday"):
        return "12:00", []
    if lowered == "midnight":
        return "00:00", []
    if not re.search(r"\d", lowered):
        guesses = next((g for phrase, g in VAGUE_TIMES.items() if phrase in lowered), ())
        return None, list(guesses)
    compact = lowered.replace(" ", "")
    match = TIME.match(compact)
    if not match:
        return None, []
    rest = compact[match.end():]
    if rest and parse_timezone(rest) is None and rest not in ("inthemorning", "intheevening", "atnight"):
        return None, []
    hour, minute = int(match.group(1)), int(match.group(2) or 0)
    suffix = (match.group(3) or "").replace(".", "") or {"intheevening": "pm", "atnight": "pm", "inthemorning": "am"}.get(rest, "")
    if minute > 59:
        return None, []
    if suffix:
        if not 1 <= hour <= 12:
            return None, []
        return f"{hour % 12 + (12 if suffix == 'pm' else 0):02d}:{minute:02d}", []
    if hour >= 24:
        return None, []
    if hour == 0 or hour > 12 or (len(match.group(1)) == 2 and match.group(2)):
        return f"{hour:02d}:{minute:02d}", []
    return None, [f"{hour}:{minute:02d} AM", f"{hour}:{minute:02d} PM"]


def parse_timezone(text: str) -> str | None:
    lowered = re.sub(r"\s*(?:time ?zone|time|timezone)$", "", plain(text)).strip()
    if not lowered or lowered in VAGUE_TIMEZONES:
        return None
    if lowered in TZ_ALIASES:
        return TZ_ALIASES[lowered]
    offset = TZ_OFFSET.fullmatch(lowered.replace(" ", ""))
    if offset and lowered.startswith(("utc", "gmt", "+", "-")):
        sign, hours, minutes = offset.group(1), int(offset.group(2)), int(offset.group(3) or 0)
        if hours <= 14 and minutes < 60:
            return f"UTC{sign}{hours:02d}:{minutes:02d}"
    candidate = normalize_text(text).replace(" ", "_")
    for guess in (candidate, "/".join(part.capitalize() for part in candidate.split("/"))):
        try:
            ZoneInfo(guess)
            return guess
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return _zone_by_city().get(lowered)


def parse_slack_channel(text: str) -> str | None:
    name = re.sub(r"\s+channel$", "", text.strip().lower()).lstrip("#").strip()
    return f"#{name}" if SLACK_CHANNEL.fullmatch(name) else None


def twelve_hour(hh_mm: str) -> str:
    hours, minutes = (int(part) for part in hh_mm.split(":"))
    return f"{hours % 12 or 12}:{minutes:02d} {'AM' if hours < 12 else 'PM'}"


@lru_cache
def _zone_by_city() -> dict[str, str]:
    table: dict[str, str] = {}
    for name in available_timezones():
        table.setdefault(name.rsplit("/", 1)[-1].replace("_", " ").lower(), name)
    return table
