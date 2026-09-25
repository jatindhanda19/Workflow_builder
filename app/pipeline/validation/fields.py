"""Per-field validation. Every value is normalised first, then checked against its field kind."""

import re
from dataclasses import dataclass, field
from typing import Callable

from app.pipeline.extraction.normalize import (
    EMAIL,
    NAME_MAX_WORDS,
    clean_name,
    is_yes_no,
    match_choice,
    normalize_text,
    parse_duration,
    parse_field_list,
    parse_mapping,
    parse_slack_channel,
    parse_time,
    parse_timezone,
    plain,
    split_list,
)
from app.registry import FieldDef
from app.state.models import FieldValue, Option

GLOBAL_VAGUE = frozenset({
    "it", "that", "this", "something", "stuff", "whatever", "anything", "idk", "not sure", "the thing",
    "you decide", "up to you", "default", "the usual",
})
PLACEHOLDER = re.compile(r"\{\s*([\w ]+?)\s*\}")
Context = dict[str, object]


@dataclass(frozen=True)
class Verdict:
    ok: bool
    value: FieldValue | None = None
    reason: str | None = None
    question: str | None = None
    options: list[Option] = field(default_factory=list)


def validate(fdef: FieldDef, raw: str, context: Context | None = None) -> Verdict:
    text = " ".join(raw.split()) if fdef.kind == "free_text" else normalize_text(raw)
    if not text:
        return Verdict(False, reason="no value was given")
    if plain(text) in GLOBAL_VAGUE or plain(text) in fdef.vague:
        return _vague(fdef, text)
    return _VALIDATORS[fdef.kind](fdef, text, context or {})


def find_placeholders(text: str) -> list[str]:
    return list(dict.fromkeys(_snake(p) for p in PLACEHOLDER.findall(text)))


def choice_options(fdef: FieldDef) -> list[Option]:
    return [
        Option(label=c.label, assign={fdef.key: c.value}, keywords=[c.value.replace("_", " "), c.label.lower(), *c.synonyms])
        for c in fdef.choices
    ]


def _vague(fdef: FieldDef, text: str) -> Verdict:
    if fdef.kind == "email_list":
        return Verdict(False, reason=f'"{text}" does not say which email address to use')
    if fdef.kind == "field_list":
        return Verdict(False, reason=f'"{text}" is too general to say which details to include')
    return Verdict(False, reason=f'"{text}" does not name a specific {fdef.label.lower()}')


def _text(fdef: FieldDef, text: str, context: Context) -> Verdict:
    if is_yes_no(text):
        return Verdict(False, reason=f'"{text}" does not give a {fdef.label.lower()}')
    return Verdict(True, text)


def _name(fdef: FieldDef, text: str, context: Context) -> Verdict:
    value = clean_name(fdef, text)
    if not value or plain(value) in GLOBAL_VAGUE or plain(value) in fdef.vague:
        return _vague(fdef, text)
    if is_yes_no(value):
        return Verdict(False, reason=f'"{text}" does not give a {fdef.label.lower()}')
    if len(value.split()) > NAME_MAX_WORDS:
        return Verdict(False, reason=f"I could not find the {fdef.label.lower()} in that sentence")
    return Verdict(True, value)


def _field_list(fdef: FieldDef, text: str, context: Context) -> Verdict:
    items = parse_field_list(text)
    return Verdict(True, items) if items else _vague(fdef, text)


def _mapping(fdef: FieldDef, text: str, context: Context) -> Verdict:
    placeholders = find_placeholders(str(context.get("template.body", "")))
    if not placeholders:
        return Verdict(False, reason="the template has no {placeholders} to fill")
    pairs = parse_mapping(text, placeholders)
    missing = [p for p in placeholders if p not in pairs]
    if missing:
        names = ", ".join(f"{{{m}}}" for m in missing)
        return Verdict(False, reason=f"I still need a data field for {names}")
    return Verdict(True, [f"{key}={pairs[key]}" for key in placeholders])


def _choice(fdef: FieldDef, text: str, context: Context) -> Verdict:
    choice = match_choice(fdef, text)
    if choice:
        return Verdict(True, choice.value)
    labels = ", ".join(c.label for c in fdef.choices[:-1]) + " or " + fdef.choices[-1].label
    if is_yes_no(text):
        return Verdict(False, reason=f'"{text}" does not tell me which one you mean',
                       question=f"Could you pick one: {labels}?", options=choice_options(fdef))
    return Verdict(False, reason=f'"{text}" is not one of the options',
                   question=f"Could you pick one: {labels}?", options=choice_options(fdef))


def _email_list(fdef: FieldDef, text: str, context: Context) -> Verdict:
    addresses, names = [], []
    for item in split_list(text):
        found = EMAIL.search(item)
        (addresses if found else names).append(found.group(0).lower() if found else item)
    if names:
        who = names[0]
        return Verdict(False, reason=f'"{who}" is a name, not an email address', question=f"What's {who}'s email address?")
    return Verdict(True, addresses)


def _time(fdef: FieldDef, text: str, context: Context) -> Verdict:
    value, guesses = parse_time(text)
    if value:
        return Verdict(True, value)
    if guesses:
        options = [Option(label=g, assign={fdef.key: parse_time(g)[0]}, keywords=[g.lower()]) for g in guesses]
        return Verdict(False, reason=f'"{text}" is not an exact time', options=options)
    return Verdict(False, reason=f'"{text}" is not a time I can read (for example 6:00 PM or 18:00)')


def _timezone(fdef: FieldDef, text: str, context: Context) -> Verdict:
    zone = parse_timezone(text)
    if zone:
        return Verdict(True, zone)
    return Verdict(False, reason=f'"{text}" is not a timezone I recognise (for example IST, UTC or Asia/Kolkata)')


def _slack_channel(fdef: FieldDef, text: str, context: Context) -> Verdict:
    channel = parse_slack_channel(text)
    if channel:
        return Verdict(True, channel)
    return Verdict(False, reason=f'"{text}" is not a Slack channel name (for example #finance)')


def _day_of_month(fdef: FieldDef, text: str, context: Context) -> Verdict:
    match = re.search(r"\b([1-9]|[12]\d|3[01])(?:st|nd|rd|th)?\b|\blast\b", text.lower())
    if match:
        return Verdict(True, match.group(1) or "last")
    return Verdict(False, reason=f'"{text}" is not a day of the month (1-31, or "last")')


def _duration(fdef: FieldDef, text: str, context: Context) -> Verdict:
    duration = parse_duration(text)
    return Verdict(True, duration) if duration else Verdict(False, reason=f'"{text}" is not a duration')


def _snake(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


_VALIDATORS: dict[str, Callable[[FieldDef, str, Context], Verdict]] = {
    "text": _text,
    "free_text": _text,
    "name": _name,
    "field_list": _field_list,
    "mapping": _mapping,
    "choice": _choice,
    "email_list": _email_list,
    "time": _time,
    "timezone": _timezone,
    "slack_channel": _slack_channel,
    "day_of_month": _day_of_month,
    "duration": _duration,
}


def same_value(a: FieldValue | None, b: FieldValue | None) -> bool:
    def norm(v: FieldValue | None) -> object:
        return sorted(plain(x) for x in v) if isinstance(v, list) else plain(v or "")

    return norm(a) == norm(b)
