"""Per-parameter validation and readiness. The LLM never decides whether a value is valid or complete."""

import re
from dataclasses import dataclass
from typing import Callable

from app.pipeline.normalize import (
    EMAIL,
    ORDERING_OPERATORS,
    UNARY_OPERATORS,
    field_subject,
    looks_like_value,
    normalize_text,
    parse_field,
    parse_operator,
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
# A condition step always has these three parameters; merge maps whatever the planner named them onto these.
FIELD, OPERATOR, VALUE = "field", "operator", "value"
# "otherwise" / "or else" name an action for the false branch; these words say the false branch does nothing.
OTHERWISE = re.compile(r"\b(?:otherwise|or else|else\s*[,:]|if not\s*[,:])", re.IGNORECASE)
FALSE_DOES_NOTHING = re.compile(
    r"\b(?:otherwise|or else|else|if not)\b[\s,:]*(?:just\s+|simply\s+)?"
    r"(?:ignore|discard|skip|drop|stop|do nothing|nothing|leave it|end)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Verdict:
    ok: bool
    value: FieldValue | None = None
    reason: str | None = None
    guesses: tuple[str, ...] = ()  # what a rejected answer may have meant, e.g. ("5:00 PM", "6:00 PM")


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
    if param is not None and param.kind == "operator":
        return value.replace("_", " ")
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
    return structure and not open_items(state) and not structure_errors(state)


Link = tuple[str, str | None]  # (parent step id, "true" / "false" when the parent is a condition)


def links(steps: list[Step]) -> dict[str, list[Link]]:
    """The parents of every step after the trigger: the edges of the workflow.

    A step follows the steps named in `after`. Without `after` it follows the step just before it, except a step
    with a `branch` whose previous step is not on that branch: it follows the nearest condition above it (so
    "condition, notify (true), save (false)" needs no `after`). Following a condition without a `branch` means
    its true outcome, as in "if X, do Y".

    An outcome starts one step. A second step put on the same outcome ("if so, add it to Airtable and send it to
    Slack") runs after the last action on that path, in the order the steps are listed."""
    kinds = {s.id: s.kind for s in steps}
    result: dict[str, list[Link]] = {}
    path: dict[str, Link | None] = {}  # the condition outcome a step runs under
    last: dict[Link, str | None] = {}  # the last step on each outcome; None after a condition (nothing to chain to)
    for index, step in enumerate(steps[1:], 1):
        previous = steps[index - 1]
        parents = step.after or [previous.id]
        if not step.after and step.branch and previous.kind != "condition":
            condition = next((s.id for s in reversed(steps[:index]) if s.kind == "condition"), None)
            if condition and path.get(previous.id) != (condition, step.branch):
                parents = [condition]
        linked = [(p, (step.branch or "true") if kinds.get(p) == "condition" else None) for p in parents]
        result[step.id] = [(last[link], None) if link[1] and last.get(link) else link for link in linked]
        first = result[step.id][0]
        path[step.id] = first if first[1] else path.get(first[0])
        if path[step.id]:
            last[path[step.id]] = step.id if step.kind == "action" else None
    return result


def structure_errors(state: WorkflowState) -> list[str]:
    """Deterministic checks of the workflow's shape before it is generated, phrased as questions to the user."""
    errors: list[str] = []
    steps, user_text = state.steps, state.user_text()
    position = {s.id: i for i, s in enumerate(steps)}
    titles = {s.id: s.title for s in steps}
    children: dict[str, list[Link]] = {s.id: [] for s in steps}
    for step in steps[1:]:
        for parent, branch in links(steps)[step.id]:
            if parent not in position:
                errors.append(f'"{step.title}" is set to come after "{parent}", which is not a step of this workflow. '
                              "Which step should it follow?")
            elif position[parent] >= position[step.id]:
                errors.append(f'"{step.title}" is set to come after "{titles[parent]}", which runs later. '
                              "Which step should it follow?")
            else:
                children[parent].append((step.id, branch))
        if step.branch and not any(s.kind == "condition" for s in steps[:position[step.id]]):
            errors.append(f'"{step.title}" is set to run only when a condition is '
                          f'{"met" if step.branch == "true" else "not met"}, but it does not follow a condition. '
                          "Should it always run?")
    conditions = [s for s in steps if s.kind == "condition"]
    for step in conditions:
        errors += _condition_errors(step)
        if not children[step.id]:
            errors.append(f"What should happen when {condition_text(step)}?")
        for outcome, word in (("true", "met"), ("false", "not met")):
            starts = [titles[c] for c, b in children[step.id] if b == outcome]
            if len(starts) > 1:
                errors.append(f"When {condition_text(step)} is {word}, both {' and '.join(starts)} come first. "
                              "Should they run one after the other?")
    # "otherwise <action>" needs a false outcome; it may end only if the user said to ignore those items.
    wants_otherwise = OTHERWISE.search(user_text) and not FALSE_DOES_NOTHING.search(user_text)
    has_false = any(b == "false" for kids in children.values() for _, b in kids)
    if conditions and wants_otherwise and not has_false:
        errors.append(f"You said what should happen otherwise, but nothing is set up for when "
                      f"{condition_text(conditions[0])} is not true. What should happen then?")
    return errors


def condition_text(step: Step) -> str:
    """E.g. "invoice.amount is greater than 100000", or "customer.email is empty"."""
    field, operator, value = (step.param(n) for n in (FIELD, OPERATOR, VALUE))
    if field and operator and None not in (field.value, operator.value):
        if operator.value in UNARY_OPERATORS:
            return f"{display(field, field.value)} {display(operator, operator.value)}"
        if value and value.value is not None:
            return f"{display(field, field.value)} is {display(operator, operator.value)} {display(value, value.value)}"
    return f'"{step.operation}"'


def _condition_errors(step: Step) -> list[str]:
    field, operator, value = (step.param(n) for n in (FIELD, OPERATOR, VALUE))
    errors = []
    if field is None or operator is None or value is None:
        return [f'The condition "{step.operation}" needs a field, a comparison and a value. What should it check?']
    if isinstance(field.value, str) and isinstance(value.value, str) and plain(field.value) == plain(value.value):
        errors.append(f'The condition "{step.operation}" compares {field.value} with itself. What should it compare with?')
    if operator.value in ORDERING_OPERATORS and isinstance(value.value, str) and not NUMBER.search(value.value):
        errors.append(f'The condition "{step.operation}" checks if {display(field, field.value)} is '
                      f'{display(operator, operator.value)} "{value.value}", which is not a number. Which number should it use?')
    return errors


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
    return Verdict(False, reason=f'"{text}" is not an exact time{hint}', guesses=tuple(guesses))


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


def _field(param: Param, text: str) -> Verdict:
    """A reference to data (invoice.amount), never a comparison value such as 10000."""
    if looks_like_value(text):
        subject = field_subject(param.label)
        if not subject:
            return Verdict(False, reason=f"I need the name of the field to check, such as amount or invoice.amount. "
                                         f"{text} looks like a value, not a field name")
        entity, last = subject[:-1], subject[-1]
        examples = [last, *FIELD_SYNONYMS.get(last, ()), *([f"{'_'.join(entity)}.{last}"] if entity else [])]
        listed = examples[0] if len(examples) == 1 else f"{', '.join(examples[:-1])}, or {examples[-1]}"
        return Verdict(False, reason=f"I need the field that contains the {' '.join(subject)}, such as {listed}. "
                                     f"{text} looks like a value, not a field name")
    field = parse_field(text, param.label)
    return Verdict(True, field) if field else Verdict(False, reason=f'"{text}" is not a field name (for example amount or invoice.amount)')


def _operator(param: Param, text: str) -> Verdict:
    operator = parse_operator(text)
    return Verdict(True, operator) if operator else Verdict(False, reason=f'"{text}" is not a comparison (for example greater than)')


def _channel(param: Param, text: str) -> Verdict:
    channel = parse_slack_channel(text)
    return Verdict(True, channel) if channel else Verdict(False, reason=f'"{text}" is not a channel name (for example #sales)')


_VALIDATORS: dict[str, Callable[[Param, str], Verdict]] = {
    "text": _text, "long_text": _text, "list": _list, "email": _email, "email_list": _email_list, "url": _url,
    "number": _number, "time": _time, "timezone": _timezone, "choice": _choice, "channel": _channel,
    "field": _field, "operator": _operator,
}
FIELD_SYNONYMS = {"amount": ("total",), "total": ("amount",), "price": ("amount",)}
