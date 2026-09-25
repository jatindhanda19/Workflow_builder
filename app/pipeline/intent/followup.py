import json
import re

from app.pipeline.intent.schema import FollowUpDecision
from app.core.llm import LLMError, StructuredLLM
from app.registry import FIELDS
from app.pipeline.validation.readiness import FieldRow

EDIT_START = re.compile(
    r"^\s*(?:please\s+)?(change|update|set|replace|switch|use|make|rename|edit|add|remove|instead)\b", re.IGNORECASE
)
EDIT_TO = re.compile(
    r"^\s*(?:please\s+)?(?:change|update|set|replace|switch|make)\s+(?:the\s+)?(.+?)\s+(?:to|with)\s+(.+?)\s*$",
    re.IGNORECASE,
)
NEW_REQUEST = re.compile(r"\b(when|whenever|every|each|send|notify|alert|remind|backup|back up|post)\b", re.IGNORECASE)
QUESTION_START = re.compile(
    r"^\s*(what|where|when|who|which|how|why|does|do|is|are|can|will|could|would)\b", re.IGNORECASE
)
IGNORED_WORDS = {"the", "a", "an", "my", "email", "line"}


def classify_followup(llm: StructuredLLM, message: str, rows: list[FieldRow], workflow: dict | None) -> FollowUpDecision:
    try:
        decision = llm.generate(FollowUpDecision, "followup", _context(message, rows, workflow))
    except LLMError:
        decision = heuristic(message, rows)
    decision.fields = [key for key in decision.fields if key in FIELDS]
    return decision


def heuristic(message: str, rows: list[FieldRow]) -> FollowUpDecision:
    """Used when the LLM is unavailable, so edits and new requests still work."""
    if EDIT_START.match(message):
        match = EDIT_TO.match(message)
        key = _field_named(match.group(1), rows) if match else None
        if match and key:
            return FollowUpDecision(kind="edit", fields=[key], value=match.group(2).strip())
        return FollowUpDecision(kind="edit")
    if "?" in message or QUESTION_START.match(message):
        return FollowUpDecision(kind="question")
    if NEW_REQUEST.search(message) and len(message.split()) >= 4:
        return FollowUpDecision(kind="new_request")
    return FollowUpDecision(kind="other")


def _field_named(words: str, rows: list[FieldRow]) -> str | None:
    """Match "recipient", "the slack channel" or "subject line" to one collected field."""
    wanted = set(re.findall(r"[a-z]+", words.lower())) - IGNORED_WORDS
    wanted |= {w.rstrip("s") for w in wanted}
    best, best_score = None, 0
    for row in rows:
        if row.status == "not_applicable":
            continue
        name = set(re.findall(r"[a-z]+", f"{row.label} {row.key.split('.')[-1]}".lower()))
        name |= {w.rstrip("s") for w in name}
        score = len(wanted & name)
        if score > best_score:
            best, best_score = row.key, score
    return best


def _context(message: str, rows: list[FieldRow], workflow: dict | None) -> str:
    table = {r.key: {"label": r.label, "value": r.value} for r in rows if r.status == "filled"}
    return "\n\n".join([
        f"FIELD KEYS: {', '.join(table)}",
        f"WORKFLOW STATE: {json.dumps(table, ensure_ascii=False)}",
        f"GENERATED WORKFLOW: {json.dumps(workflow, ensure_ascii=False)}",
        f"USER MESSAGE:\n{message}",
    ])
