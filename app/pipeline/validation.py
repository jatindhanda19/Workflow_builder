"""Per-parameter validation and readiness. The LLM never decides whether a value is valid or complete."""

import re
from dataclasses import dataclass
from typing import Callable

from app.pipeline.normalize import (
    EMAIL,
    normalize_text,
    parse_slack_channel,
    parse_time,
    parse_timezone,
    plain,
    split_list,
    twelve_hour,
)
from app.state.models import APP, FieldValue, Param, Step, WorkflowState

VAGUE = frozenset({
    "it", "that", "this", "something", "stuff", "whatever", "anything", "idk", "not sure", "the thing",
    "you decide", "up to you", "default", "the usual", "somewhere", "someone", "anyone", "everyone",
})
# n8n-style reference to data of an earlier step, e.g. {{shopify_trigger.customer_email}}: valid for any kind.
EXPRESSION = re.compile(r"\{\{\s*[\w.\[\]$ -]+\s*\}\}")
NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
URL = re.compile(r"^(?:https?://)?[\w-]+(?:\.[\w-]+)+(?:[/?#]\S*)?$", re.IGNORECASE)


@dataclass(frozen=True)
class Verdict:
    ok: bool
    value: FieldValue | None = None
    reason: str | None = None


@dataclass(frozen=True)
class OpenItem:
    key: str  # "<step id>.<param>" or "<step id>.app"
    step: Step
    param: Param | None  # None: the step's app is missing


@dataclass(frozen=True)
class Row:
    key: str
    step: str
    label: str
    value: FieldValue | None
    status: str  # filled | missing | ambiguous
    note: str | None
    required: bool


def validate(param: Param, raw: FieldValue) -> Verdict:
    text = ", ".join(raw) if isinstance(raw, list) else str(raw)
    text = " ".join(text.split()) if param.kind == "long_text" else normalize_text(text)
    if not text:
        return Verdict(False, reason="no value was given")
    if EXPRESSION.fullmatch(text) or (param.kind in ("text", "long_text") and EXPRESSION.search(text)):
        return Verdict(True, text)
    if plain(text) in VAGUE:
        return Verdict(False, reason=f'"{text}" does not name a specific {param.label.lower()}')
    return _VALIDATORS[param.kind](param, text)


def display(param: Param | None, value: FieldValue | None) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(value)
    if param is not None and param.kind == "time" and re.fullmatch(r"\d{2}:\d{2}", value):
        return twelve_hour(value)
    return value


def open_items(state: WorkflowState) -> list[OpenItem]:
    """Everything still missing, in workflow order: each step's app first, then its required parameters."""
    items: list[OpenItem] = []
    for step in state.steps:
        if not step.app:
            items.append(OpenItem(f"{step.id}.{APP}", step, None))
        items += [OpenItem(f"{step.id}.{p.name}", step, p) for p in step.params if p.required and p.value is None]
    return items


def is_ready(state: WorkflowState) -> bool:
    kinds = [s.kind for s in state.steps]
    structure = bool(kinds) and kinds[0] == "trigger" and kinds.count("trigger") == 1 and "action" in kinds
    return structure and not open_items(state)


def rows(state: WorkflowState) -> list[Row]:
    table: list[Row] = []
    for step in state.steps:
        status = "filled" if step.app else "missing"
        table.append(Row(f"{step.id}.{APP}", step.title, "App", step.app, status, None, True))
        for p in step.params:
            if p.value is None and not p.required:
                continue
            status = "filled" if p.value is not None else ("ambiguous" if p.note else "missing")
            table.append(Row(f"{step.id}.{p.name}", step.title, p.label, p.value, status, p.note, p.required))
    return table


# -- validators -----------------------------------------------------------------------------------------
def _text(param: Param, text: str) -> Verdict:
    return Verdict(True, text)


def _list(param: Param, text: str) -> Verdict:
    items = split_list(text)
    return Verdict(True, items) if items else Verdict(False, reason=f"I could not read a list of {param.label.lower()}")


def _email(param: Param, text: str) -> Verdict:
    found = EMAIL.fullmatch(text.strip())
    if found:
        return Verdict(True, text.strip().lower())
    return Verdict(False, reason=f'"{text}" is not an email address')


def _email_list(param: Param, text: str) -> Verdict:
    items = split_list(text)
    bad = next((i for i in items if not EMAIL.fullmatch(i) and not EXPRESSION.fullmatch(i)), None)
    if bad or not items:
        return Verdict(False, reason=f'"{bad or text}" is not an email address')
    return Verdict(True, [i.lower() if EMAIL.fullmatch(i) else i for i in items])


def _url(param: Param, text: str) -> Verdict:
    if not URL.fullmatch(text):
        return Verdict(False, reason=f'"{text}" is not a URL')
    return Verdict(True, text if re.match(r"https?://", text, re.IGNORECASE) else f"https://{text}")


def _number(param: Param, text: str) -> Verdict:
    match = NUMBER.search(text)
    if not match:
        return Verdict(False, reason=f'"{text}" is not a number')
    return Verdict(True, match.group(0).replace(",", ""))


def _time(param: Param, text: str) -> Verdict:
    value, guesses = parse_time(text)
    if value:
        return Verdict(True, value)
    hint = f" (for example {' or '.join(guesses)})" if guesses else " (for example 6:00 PM)"
    return Verdict(False, reason=f'"{text}" is not an exact time{hint}')


def _timezone(param: Param, text: str) -> Verdict:
    zone = parse_timezone(text)
    return Verdict(True, zone) if zone else Verdict(False, reason=f'"{text}" is not a timezone I recognise')


def _choice(param: Param, text: str) -> Verdict:
    if not param.choices:
        return Verdict(True, text)
    lowered = plain(text)
    exact = next((c for c in param.choices if plain(c) == lowered), None)
    contained = [c for c in param.choices if plain(c) and re.search(rf"(?<!\w){re.escape(plain(c))}(?!\w)", lowered)]
    choice = exact or (contained[0] if len(contained) == 1 else None)
    if choice:
        return Verdict(True, choice)
    return Verdict(False, reason=f'"{text}" is not one of the options')


def _channel(param: Param, text: str) -> Verdict:
    channel = parse_slack_channel(text)
    return Verdict(True, channel) if channel else Verdict(False, reason=f'"{text}" is not a channel name (for example #sales)')


_VALIDATORS: dict[str, Callable[[Param, str], Verdict]] = {
    "text": _text, "long_text": _text, "list": _list, "email": _email, "email_list": _email_list, "url": _url,
    "number": _number, "time": _time, "timezone": _timezone, "choice": _choice, "channel": _channel,
}
