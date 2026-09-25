
import re
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from app.registry import NO, YES, Choice, FieldDef

WHITESPACE = re.compile(r"\s+")
NAME_FILLERS = re.compile(
    r"^(?:(?:it'?s|its|it|is|this is|that is|that'?s|the|a|an|called|named|name is|name|use|using|watch|"
    r"watching|monitor|check|in|on|from|of|please|i want|i mean|yes|ok|okay|so|just|my|our)\s+)+",
    re.IGNORECASE,
)
LIST_FILLERS = re.compile(
    r"^(?:(?:it|it'?s|its|they|should|would|will|must|include|includes|including|contain|contains|containing|"
    r"have|has|with|show|shows|list|lists|mention|the|a|an|please|just|and|about|of|all)\s+)+",
    re.IGNORECASE,
)
LIST_SPLIT = re.compile(r"\s*(?:,|;|\band\b|&)\s*", re.IGNORECASE)
EMAIL = re.compile(r"[^@\s,;<>()]+@[^@\s,;<>()]+\.[a-z]{2,}", re.IGNORECASE)
SLACK_CHANNEL = re.compile(r"[a-z0-9][a-z0-9_-]{0,79}")
TIME = re.compile(r"(\d{1,2})(?::|\.)?(\d{2})?\s*(am|pm|a\.m\.|p\.m\.)?")
TZ_OFFSET = re.compile(r"(?:utc|gmt)?\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?")
DURATION = re.compile(r"(\d+)\s*(second|sec|minute|min|hour|hr|day|week)s?", re.IGNORECASE)
NAME_MAX_WORDS = 6

FIELD_GROUPS: dict[str, tuple[str, ...]] = {
    "details about the changes": ("changed_cell", "old_value", "new_value", "changed_by"),
    "details about changes": ("changed_cell", "old_value", "new_value", "changed_by"),
    "details of the change": ("changed_cell", "old_value", "new_value", "changed_by"),
    "change details": ("changed_cell", "old_value", "new_value", "changed_by"),
    "what changed": ("changed_cell", "old_value", "new_value"),
    "all fields": ("all_fields",),
    "everything": ("all_fields",),
}
FIELD_ALIASES: dict[str, str] = {
    "old value": "old_value", "previous value": "old_value", "old status": "old_value",
    "new value": "new_value", "new status": "new_value", "current value": "new_value", "updated value": "new_value",
    "who": "changed_by", "who updated it": "changed_by", "who changed it": "changed_by", "updated by": "changed_by",
    "changed by": "changed_by", "editor": "changed_by", "when": "changed_at", "timestamp": "changed_at",
    "changed at": "changed_at", "cell": "changed_cell", "changed cell": "changed_cell", "which cell": "changed_cell",
    "row": "row_data", "whole row": "row_data", "row data": "row_data", "full row": "row_data",
}
VAGUE_ITEMS = frozenset({"details", "info", "information", "stuff", "things", "data"})
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


def contains_phrase(haystack: str, phrase: str) -> bool:
    return re.search(rf"(?<![\w#]){re.escape(phrase)}(?![\w])", haystack) is not None


def is_yes_no(text: str) -> bool:
    words = plain(text).split()
    return bool(words) and len(words) <= 3 and (words[0] in YES or words[0] in NO)


def match_choice(fdef: FieldDef, text: str) -> Choice | None:
    lowered = plain(text)
    stripped = NAME_FILLERS.sub("", lowered).strip()
    for candidate in (lowered, stripped):
        for choice in fdef.choices:
            if candidate in {choice.value, choice.value.replace("_", " "), choice.label.lower(), *choice.synonyms}:
                return choice
    words = lowered.split()
    if words and (words[0] in YES or words[0] in NO):
        polarity = YES if words[0] in YES else NO
        hits = [c for c in fdef.choices if any(s in polarity for s in c.synonyms)]
        if len(hits) == 1:
            return hits[0]
    hits = [
        choice for choice in fdef.choices
        if any(contains_phrase(lowered, name) for name in (choice.label.lower(), *choice.synonyms)
               if len(name) >= 4 or " " in name)
    ]
    return hits[0] if len(hits) == 1 else None


def clean_name(fdef: FieldDef, text: str) -> str:
    value = normalize_text(text)
    for _ in range(3):
        before = value
        value = NAME_FILLERS.sub("", value).strip()
        for noun in fdef.nouns:
            if len(value.split()) > len(noun.split()):
                pattern = re.escape(noun)
                value = re.sub(rf"^(?:the\s+)?{pattern}\s+(?:is\s+|called\s+|named\s+)?", "", value, flags=re.IGNORECASE)
                value = re.sub(rf"\s+{pattern}$", "", value, flags=re.IGNORECASE)
        value = normalize_text(value)
        if value == before:
            break
    return value


def parse_field_list(text: str) -> list[str]:
    lowered = WHITESPACE.sub(" ", re.sub(r"[^\w\s,;&-]", " ", text.lower())).strip()
    items: list[str] = []
    for phrase, group in sorted(FIELD_GROUPS.items(), key=lambda item: -len(item[0])):
        if contains_phrase(lowered, phrase):
            items.extend(group)
            lowered = re.sub(rf"(?<![\w]){re.escape(phrase)}(?![\w])", ",", lowered)
    for raw in LIST_SPLIT.split(lowered):
        item = LIST_FILLERS.sub("", raw.strip() + " ").strip()
        # "details about the invoice" is as vague as "details": it names no specific field.
        if item and item.split()[0] not in VAGUE_ITEMS:
            items.append(FIELD_ALIASES.get(item) or re.sub(r"[^a-z0-9]+", "_", item).strip("_"))
    return _dedupe([i for i in items if i])


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


def parse_duration(text: str) -> str | None:
    match = DURATION.search(text)
    if not match:
        return None
    unit = {"sec": "second", "min": "minute", "hr": "hour"}.get(match.group(2).lower(), match.group(2).lower())
    count = int(match.group(1))
    return f"{count} {unit}{'s' if count != 1 else ''}"


def parse_mapping(text: str, placeholders: list[str]) -> dict[str, str]:
    """Read "amount from the Amount column, client_name from Name" into {placeholder: data field}."""
    pairs: dict[str, str] = {}
    for part in re.split(r"\s*(?:,|;|\band\b)\s*", normalize_text(text), flags=re.IGNORECASE):
        match = re.match(r"\{?([\w ]+?)\}?\s*(?:=|:|->|→|from|comes from|is|uses)\s*(?:the\s+)?(.+)$", part, re.IGNORECASE)
        if match:
            key = _snake(match.group(1))
            if key in placeholders:
                pairs[key] = _strip_column(match.group(2))
    if not pairs and len(placeholders) == 1 and text.strip():
        pairs[placeholders[0]] = _strip_column(normalize_text(text))
    return pairs


def _strip_column(text: str) -> str:
    text = re.sub(r"^(?:the\s+)?", "", text.strip(), flags=re.IGNORECASE)
    return normalize_text(re.sub(r"\s+(?:column|field)$", "", text, flags=re.IGNORECASE))


def _snake(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _dedupe(items: list[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen


@lru_cache
def _zone_by_city() -> dict[str, str]:
    table: dict[str, str] = {}
    for name in available_timezones():
        table.setdefault(name.rsplit("/", 1)[-1].replace("_", " ").lower(), name)
    return table
