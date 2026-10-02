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
# A comparison value such as 10000, ₹1,00,000, 10 MB or 50% (never a field name).
VALUE_LIKE = re.compile(
    r"(?:[₹$€£]|rs\.?|inr|usd)?\s*-?\d[\d,]*(?:\.\d+)?\s*"
    r"(?:%|k|kb|mb|gb|tb|lakhs?|lacs?|crores?|cr|thousand|million|rs|inr|usd|rupees|dollars|bytes)?",
    re.IGNORECASE,
)
FIELD_PATH = re.compile(r"[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)*")
# Words of a condition's field label that do not name the data, e.g. "Invoice amount field".
FIELD_LABEL_NOISE = frozenset({
    "field", "fields", "name", "column", "property", "value", "data", "the", "which", "of", "to", "check", "compare",
    "condition", "reference", "key",
})
# Canonical comparison operators and the words users write for them (matched exactly, not as substrings).
OPERATORS: dict[str, tuple[str, ...]] = {
    "greater_than_or_equal": (">=", "≥", "=>", "greater than or equal to", "greater than or equal", "at least",
                              "more than or equal to", "no less than", "minimum", "gte"),
    "less_than_or_equal": ("<=", "≤", "=<", "less than or equal to", "less than or equal", "at most",
                           "no more than", "maximum", "up to", "lte"),
    "greater_than": (">", "greater than", "more than", "above", "over", "exceeds", "higher than", "larger than",
                     "bigger than", "gt"),
    "less_than": ("<", "less than", "below", "under", "lower than", "smaller than", "fewer than", "lt"),
    "not_equals": ("!=", "≠", "<>", "not equals", "not equal to", "not equal", "does not equal", "is not", "isn't", "ne"),
    "equals": ("=", "==", "equals", "equal to", "equal", "is equal to", "is", "same as", "eq"),
    "contains": ("contains", "includes", "has", "with"),
    "not_contains": ("does not contain", "not contains", "doesn't contain", "excludes"),
    # Checks with no value to compare with.
    "is_empty": ("is empty", "empty", "is missing", "missing", "is blank", "blank", "is not provided", "not provided",
                 "does not exist", "is null", "has no value"),
    "is_not_empty": ("is not empty", "not empty", "is present", "present", "exists", "is provided", "provided",
                     "is not missing", "is not blank", "has a value"),
}
ORDERING_OPERATORS = frozenset({"greater_than", "greater_than_or_equal", "less_than", "less_than_or_equal"})
UNARY_OPERATORS = frozenset({"is_empty", "is_not_empty"})
OPERATOR_SYMBOLS = {
    "greater_than": ">", "greater_than_or_equal": ">=", "less_than": "<", "less_than_or_equal": "<=",
    "equals": "=", "not_equals": "!=", "contains": "contains", "not_contains": "does not contain",
    "is_empty": "is empty", "is_not_empty": "is not empty",
}
# Phrases that state a comparison inside a sentence; one-word ones that also mean other things are left out.
_STATED_OPERATORS = sorted(
    (w for c, words in OPERATORS.items() if c in ORDERING_OPERATORS or c.endswith("equals") for w in words
     if w not in ("is", "equal", "eq", "ne", "gt", "lt", "gte", "lte", "minimum", "maximum", "up to")),
    key=len, reverse=True)
# "if the order total is above ₹50,000" → field "order total", operator "above", value "₹50,000";
# "if the customer's email is missing" → field "customer's email", operator "missing", no value.
_STATED_FIELD = (r"\b(?:if|when|whenever|where|whether)\s+(?:the\s+|its\s+|their\s+|an?\s+)?"
                 r"(?P<field>[a-z][a-z0-9_.]*(?:['’]s)?(?:\s+[a-z][a-z0-9_.]*){0,3}?)\s+")
STATED_CONDITION = re.compile(
    _STATED_FIELD + r"(?:is\s+|are\s+|was\s+|gets\s+)?"
    rf"(?P<operator>{'|'.join(re.escape(w) for w in _STATED_OPERATORS)})\s*"
    r"(?P<value>(?:[₹$€£]|rs\.?\s*)?-?\d(?:[\d,]*\d)?(?:\.\d+)?(?:\s*(?:%|k|kb|mb|gb|tb|lakhs?|crores?)\b)?)",
    re.IGNORECASE,
)
STATED_PRESENCE = re.compile(
    _STATED_FIELD + r"(?P<operator>(?:is|are)\s+(?:not\s+)?(?:missing|empty|blank|provided|present)|"
    r"(?:does|do)\s+not\s+exist|exists)\b",
    re.IGNORECASE,
)


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


def looks_like_value(text: str) -> bool:
    return bool(VALUE_LIKE.fullmatch(text.strip()))


def parse_field(text: str, label: str = "") -> str | None:
    """A data reference such as invoice.amount; "#amount" becomes "<entity>.amount" when the label names the entity."""
    name = re.sub(r"^the\s+|\s+(?:field|column|property)$", "", text.strip().lstrip("#$@").strip().lower())
    name = re.sub(r"[\s-]+", "_", name.strip())
    if not FIELD_PATH.fullmatch(name):
        return None
    entity = field_subject(label)[:-1]
    if not entity or "." in name:
        return name
    parts = name.split("_")
    if parts[:len(entity)] == entity:  # "invoice amount" → invoice.amount
        parts = parts[len(entity):] or parts
    return f"{'_'.join(entity)}.{'_'.join(parts)}"


def field_subject(label: str) -> list[str]:
    """The words of a field label that name the data: "Invoice amount field" → ["invoice", "amount"]."""
    return [w for w in re.findall(r"[a-z0-9]+", label.lower()) if w not in FIELD_LABEL_NOISE]


def stated_conditions(text: str) -> list[tuple[str | None, str, str | None]]:
    """Conditions written in the user's own words, as (field, operator, value) raw text, in the order written.
    A field of several words becomes a reference: "order total" → order.total, "customer's email" → customer.email.
    A presence check ("is missing") has no value."""
    matches = sorted([*STATED_CONDITION.finditer(text), *STATED_PRESENCE.finditer(text)], key=lambda m: m.start())
    found = []
    for match in matches:
        words = re.sub(r"['’]s\b", "", match.group("field").lower()).split()
        if words[0] in ("it", "this", "that", "they", "one", "something", "anything"):
            field = None  # "if it is above 500" does not say which data
        else:
            field = words[0] if len(words) == 1 else f"{words[0]}.{'_'.join(words[1:])}"
        value = match.groupdict().get("value")
        found.append((field, match.group("operator"), value.strip() if value else None))
    return found


def parse_operator(text: str) -> str | None:
    lowered = WHITESPACE.sub(" ", text.lower().replace("_", " ")).strip()
    for candidate in (lowered, re.sub(r"^(?:is|if|when)\s+", "", lowered)):
        for canonical, words in OPERATORS.items():
            if candidate == canonical.replace("_", " ") or candidate in words:
                return canonical
    return None


def twelve_hour(hh_mm: str) -> str:
    hours, minutes = (int(part) for part in hh_mm.split(":"))
    return f"{hours % 12 or 12}:{minutes:02d} {'AM' if hours < 12 else 'PM'}"


@lru_cache
def _zone_by_city() -> dict[str, str]:
    table: dict[str, str] = {}
    for name in available_timezones():
        table.setdefault(name.rsplit("/", 1)[-1].replace("_", " ").lower(), name)
    return table
