"""Readiness is decided here, in code. The LLM has no say in whether a workflow is complete."""

import re
from dataclasses import dataclass

from app.pipeline.extraction.normalize import EMAIL
from app.pipeline.planning import Plan
from app.registry import NODES, FieldDef
from app.state.models import FieldStatus, FieldValue, WorkflowState

HH_MM = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
SNAKE = re.compile(r"[a-z0-9_]+")


@dataclass(frozen=True)
class FieldRow:
    key: str
    node: str
    label: str
    value: FieldValue | None
    status: FieldStatus
    note: str | None
    required: bool


@dataclass(frozen=True)
class Readiness:
    rows: list[FieldRow]
    open_conflicts: int

    @property
    def ready(self) -> bool:
        return self.open_conflicts == 0 and all(r.status == "filled" for r in self.rows if r.required)

    @property
    def open_rows(self) -> list[FieldRow]:
        return [r for r in self.rows if r.required and r.status in ("missing", "ambiguous")]


def stored_value_is_valid(fdef: FieldDef, value: FieldValue | None) -> bool:
    """Independent re-check of what is stored, so a bad value can never reach generation."""
    if value in (None, "", []):
        return False
    checks = {
        "choice": lambda v: fdef.choice(v) is not None,
        "email_list": lambda v: isinstance(v, list) and all(EMAIL.fullmatch(x) for x in v),
        "time": lambda v: isinstance(v, str) and HH_MM.fullmatch(v) is not None,
        "field_list": lambda v: isinstance(v, list) and all(SNAKE.fullmatch(x) for x in v),
        "mapping": lambda v: isinstance(v, list) and all("=" in x for x in v),
        "slack_channel": lambda v: isinstance(v, str) and v.startswith("#"),
        "name": lambda v: isinstance(v, str) and 0 < len(v.split()) <= 6,
    }
    check = checks.get(fdef.kind, lambda v: isinstance(v, str) and bool(v.strip()))
    return check(value)


def evaluate(state: WorkflowState, plan: Plan) -> Readiness:
    rows = [_row(state, plan, fdef) for fdef in plan.fields]
    rows = [r for r in rows if r.required or r.status in ("filled", "ambiguous")]
    rows += [
        _make(fdef, None, "not_applicable", "does not apply to this workflow", False)
        for fdef in plan.not_applicable
        if fdef.required
    ]
    return Readiness(rows=rows, open_conflicts=len(state.conflicts))


def _row(state: WorkflowState, plan: Plan, fdef: FieldDef) -> FieldRow:
    required = plan.is_required(fdef)
    entry = state.fields.get(fdef.key)
    if entry is None:
        return _make(fdef, None, "missing", None, required)
    if entry.status == "ambiguous":
        return _make(fdef, None, "ambiguous", entry.reason, required)
    if stored_value_is_valid(fdef, entry.value):
        note = "derived from your answers" if entry.source == "derived" else None
        return _make(fdef, entry.value, "filled", note, required)
    return _make(fdef, entry.value, "ambiguous", "stored value failed validation", required)


def _make(fdef: FieldDef, value: FieldValue | None, status: FieldStatus, note: str | None, required: bool) -> FieldRow:
    return FieldRow(fdef.key, NODES[fdef.node].label, fdef.label, value, status, note, required)
