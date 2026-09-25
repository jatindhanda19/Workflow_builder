"""Deterministic next-question selection.

The field is chosen in code: first open conflict, then the first open required
field in plan order (phase → node stage → field order), skipping fields whose
depends_on fields are still open. The LLM may only reword the chosen question;
if its wording drops an option or a quoted phrase, the template is used.
"""

import json
import re
from dataclasses import dataclass

from app.pipeline.extraction.extract import format_history
from app.core.llm import LLMError, StructuredLLM
from app.pipeline.extraction.schema import PhrasedQuestion
from app.pipeline.planning import Plan
from app.registry import FIELDS, NODES, FieldDef
from app.registry.display import display_value, plural
from app.state.models import FieldEntry, TargetKind, WorkflowState
from app.pipeline.validation.fields import find_placeholders

MAX_QUESTION_LENGTH = 400
RECENT_MESSAGES = 6
STILL_NEED = "I still need this before I can continue."
ACK_PREFIX = re.compile(
    r"^(?:got it|thanks|thank you|great|perfect|okay|ok|sure|noted|understood|alright)\b[^?]*?[.!,:]\s*",
    re.IGNORECASE,
)
GOAL_PHRASES = {
    "send_document": "sending the {entity}", "notify": "the notification", "report": "the report",
    "backup": "saving the {entity}", "sync_data": "copying the data", "post_message": "posting the message",
}
CHANNEL_QUESTIONS = {
    "notify": "How should you be notified", "send_document": "How should the {entity} be sent",
    "report": "Where should the report go", "post_message": "Where should the message be posted",
    "sync_data": "Where should the data go", "backup": "Where should the backup go",
}
DEFAULT_ITEMS = {"sheet_event": "row update", "new_email": "email", "new_file": "file", "form_submission": "response"}


@dataclass(frozen=True)
class Target:
    key: str
    kind: TargetKind


def select_target(state: WorkflowState, plan: Plan) -> Target | None:
    if state.conflicts:
        return Target(state.conflicts[0].key, "conflict")
    open_fields = [fd for fd in plan.fields if plan.is_required(fd) and not state.is_filled(fd.key)]
    open_keys = {fd.key for fd in open_fields}
    ready = [fd for fd in open_fields if not open_keys.intersection(fd.depends_on)] or open_fields
    if not ready:
        return None
    entry = state.fields.get(ready[0].key)
    return Target(ready[0].key, "ambiguous" if entry is not None and entry.status == "ambiguous" else "missing")


def target_detail(state: WorkflowState, target: Target) -> str:
    """Identifies one specific question, used to log it and to detect repeats."""
    entry = state.fields.get(target.key)
    if target.kind == "ambiguous" and entry is not None and entry.phrase:
        return f"ambiguous: {entry.phrase}"
    return target.kind


def base_question(state: WorkflowState, target: Target) -> str:
    fdef = FIELDS[target.key]
    if target.kind == "conflict":
        conflict = state.conflicts[0]
        old, new = display_value(fdef, conflict.old), display_value(fdef, conflict.new)
        return f'Earlier you said the {fdef.label.lower()} is "{old}", but now "{new}". Should I replace it with "{new}"? (yes/no)'
    entry = state.fields.get(target.key)
    repeated = target_detail(state, target) in state.asked_details(target.key)
    if repeated:
        keep = entry is not None and entry.options and entry.question
        return f"{STILL_NEED} {entry.question if keep else render_template(state, fdef)}"
    if target.kind == "ambiguous" and entry is not None:
        return _ambiguous_question(state, fdef, entry)
    return render_template(state, fdef)


def phrase_question(llm: StructuredLLM, state: WorkflowState, target: Target, base: str) -> str:
    if base.startswith(STILL_NEED) or target.kind == "conflict":
        return base
    try:
        phrased = llm.generate(PhrasedQuestion, "question_phrasing", _context(state, target, base))
    except LLMError:
        return base
    text = ACK_PREFIX.sub("", phrased.question.strip()).strip()
    text = text[:1].upper() + text[1:]
    return text if _keeps_meaning(base, text, _option_labels(state, target)) else base


def render_template(state: WorkflowState, fdef: FieldDef) -> str:
    return fdef.question.format_map(_placeholders(state, fdef))


def _ambiguous_question(state: WorkflowState, fdef: FieldDef, entry: FieldEntry) -> str:
    if entry.question:
        return entry.question
    reason = (entry.reason or "I could not use that answer").rstrip(".")
    if entry.options:
        return f"{reason}. Did you mean {_join([o.label for o in entry.options])}?"
    return f"{reason}. {render_template(state, fdef)}"


def _placeholders(state: WorkflowState, fdef: FieldDef) -> dict[str, str]:
    values = state.values()
    intent = state.intent
    entity = intent.main_entity or "document"
    recipient = (intent.recipient_entity or "recipient").rstrip("s")
    goal = str(values.get("goal.action"))
    column = values.get("sheet_trigger.watched_column")
    # Any answered field of the same node can be referenced by name, e.g. {team} in a new Teams node.
    siblings = {fd.name: display_value(fd, values[fd.key]) for fd in NODES[fdef.node].fields if fd.key in values}
    return {
        "options": _join([c.label for c in fdef.choices]),
        "goal_phrase": GOAL_PHRASES.get(goal, "this workflow").format(entity=entity),
        "channel_question": CHANNEL_QUESTIONS.get(goal, "Where should the result go").format(entity=entity),
        "entity": entity,
        "item": intent.main_entity or DEFAULT_ITEMS.get(str(values.get("trigger.kind")), "event"),
        "recipient": recipient,
        "recipients": plural(recipient),
        "provider": display_value(FIELDS["email_trigger.provider"], values.get("email_trigger.provider")) or "mailbox",
        "file_name": str(values.get("sheet_trigger.file_name", "the file")),
        "workspace": str(values.get("slack.workspace", "your workspace")),
        "field": str(values.get("condition.field", "the value")),
        "change_phrase": f"any change to {column}" if column else "any change",
        "content_options": _join([c.label for c in FIELDS["email.content_mode"].choices]),
        "placeholders": ", ".join("{" + p + "}" for p in find_placeholders(str(values.get("template.body", "")))),
        **siblings,
    }


def _option_labels(state: WorkflowState, target: Target) -> list[str]:
    entry = state.fields.get(target.key)
    if entry is not None and entry.options:
        return [o.label for o in entry.options]
    return [c.label for c in FIELDS[target.key].choices]


def _keeps_meaning(base: str, phrased: str, options: list[str]) -> bool:
    if not phrased or "?" not in phrased or len(phrased) > MAX_QUESTION_LENGTH or phrased.count("?") > 2:
        return False
    required = re.findall(r'"([^"]+)"', base) + [o for o in options if o.lower() in base.lower()]
    return all(item.lower() in phrased.lower() for item in required)


def _context(state: WorkflowState, target: Target, base: str) -> str:
    fdef = FIELDS[target.key]
    return "\n\n".join([
        f"Field to ask about: {fdef.label}: {fdef.description}",
        f"Question kind: {target.kind}",
        f"Draft question (keep its meaning, options and quoted phrases): {base}",
        f"Already collected: {json.dumps(state.values(), ensure_ascii=False)}",
        f"Recent conversation:\n{format_history(state.messages[-RECENT_MESSAGES:])}",
    ])


def _join(items: list[str]) -> str:
    if not items:
        return ""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " or " + items[-1]
