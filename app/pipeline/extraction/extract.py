"""Structured extraction on every message, against every field that is open or could still open."""

import json

from app.pipeline.extraction.schema import Extraction
from app.core.llm import StructuredLLM
from app.pipeline.planning import Plan
from app.registry import FIELDS, WORKFLOW_NODES, FieldDef
from app.state.models import Message, WorkflowState

RECENT_MESSAGES = 6
LATEST_MARKER = "LATEST USER MESSAGE:"


def extract(llm: StructuredLLM, state: WorkflowState, plan: Plan) -> Extraction:
    return llm.generate(Extraction, "extractor", build_context(state, plan))


def candidate_fields(state: WorkflowState, plan: Plan) -> list[FieldDef]:
    """Fields of the planned nodes, plus every trigger/action field while those are undecided."""
    values = state.values()
    keys = [fd.key for p in plan.nodes for fd in p.node.fields]
    for selector, category in (("trigger.kind", "trigger"), ("action.channel", "action")):
        if selector not in values:
            keys += [fd.key for n in WORKFLOW_NODES if n.category == category for fd in n.fields]
    return [FIELDS[k] for k in dict.fromkeys(keys)]


def build_context(state: WorkflowState, plan: Plan) -> str:
    values = state.values()
    target = FIELDS.get(state.target_field or "")
    sections = [
        f"FIELD CATALOGUE:\n{_describe(candidate_fields(state, plan))}",
        f"GOAL: {values.get('goal.action', 'unknown')}; ENTITIES: {', '.join(state.intent.entities) or 'none'}",
        f"ALREADY FILLED: {json.dumps(values, ensure_ascii=False)}",
        f"OPEN FIELDS: {', '.join(fd.key for fd in plan.fields if fd.key not in values) or 'none'}",
        f"ASSISTANT'S LAST QUESTION WAS ABOUT: {target.key if target else 'nothing yet'}",
        f"RECENT CONVERSATION:\n{format_history(state.messages[:-1][-RECENT_MESSAGES:])}",
        f"{LATEST_MARKER}\n{state.latest_user_message}",
    ]
    return "\n\n".join(sections)


def format_history(messages: list[Message]) -> str:
    return "\n".join(f"{m.role}: {m.content}" for m in messages) or "(start of conversation)"


def _describe(fields: list[FieldDef]) -> str:
    lines = []
    for fd in fields:
        extra = f" choices: {' | '.join(c.value for c in fd.choices)}" if fd.choices else f" ({fd.kind})"
        lines.append(f"- {fd.key}: {fd.description}.{extra}")
    return "\n".join(lines)
