"""Gap detection: deterministic checks that a workflow is complete, run after every planner turn.

    - exactly one trigger, and at least one real action
    - every {{step.field}} reference points to an EARLIER step, and to a field that step produces according to
      the app catalogue (steps outside the catalogue can produce anything, so only their position is checked)
    - every condition has its outcomes set up, or explicitly left empty (validation.structure_errors)

A gap is turned into a targeted question; a missing step is never invented. A broken reference is cleared from
the value that holds it, with the reason as the question's note and the valid references as its first options.
"""

import re
from collections.abc import Iterator

from app.catalogue import get
from app.pipeline.validation import links, structure_errors
from app.state.models import PLACEHOLDER_OPERATIONS, FieldValue, Step, WorkflowState

REFERENCE = re.compile(r"\{\{\s*([A-Za-z_][\w-]*)((?:\.[\w$\[\]-]+)*)\s*\}\}")
MAX_SUGGESTIONS = 4


def references(value: FieldValue | None) -> Iterator[tuple[str, str, str]]:
    """Every {{step.field}} in a value: (the reference as written, step id, field path, "" for the whole step)."""
    for text in (value if isinstance(value, list) else [value or ""]):
        for match in REFERENCE.finditer(str(text)):
            yield match.group(0), match.group(1), match.group(2).lstrip(".")


def upstream(state: WorkflowState, step_id: str) -> list[Step]:
    """The steps that run before `step_id` on its path, in workflow order: the only ones whose data it can use."""
    parents = links(state.steps)
    seen: set[str] = set()
    stack = [p for p, _ in parents.get(step_id, [])]
    while stack:
        current = stack.pop()
        if current not in seen and current != step_id:
            seen.add(current)
            stack += [p for p, _ in parents.get(current, [])]
    return [s for s in state.steps if s.id in seen]


def reference_problem(state: WorkflowState, step: Step, value: FieldValue | None) -> str | None:
    """Why a reference in `value` cannot work in `step`, or None when every reference is fine."""
    earlier = {s.id: s for s in upstream(state, step.id)}
    for written, step_id, field in references(value):
        source = earlier.get(step_id)
        if source is None:
            other = state.step(step_id)
            if other is None:
                return f'{written} uses a step "{step_id}" that is not part of this workflow'
            return f'{written} uses "{other.title}", which does not run before this step, so its data is not there'
        entry = get(source.catalogue_key) if source.catalogue_key else None
        if entry is not None and field and not entry.produces(field):
            gives = ", ".join(f.name for f in entry.output_fields) or "no data of its own"
            return f'{written} asks "{source.title}" for "{field}", but it gives {gives}'
    return None


def suggestions(state: WorkflowState, step: Step) -> list[str]:
    """References to data the step can use: the catalogue fields of the steps before it."""
    found = []
    for source in upstream(state, step.id):
        entry = get(source.catalogue_key) if source.catalogue_key else None
        found += [f"{{{{{source.id}.{f.name}}}}}" for f in (entry.output_fields if entry else ())]
    return found[:MAX_SUGGESTIONS]


def completeness_gaps(state: WorkflowState) -> list[str]:
    """Everything that keeps the workflow from being complete, as plain sentences (for the turn log and tests)."""
    if not state.steps:
        return []
    gaps = []
    triggers = [s for s in state.steps if s.kind == "trigger"]
    if len(triggers) != 1:
        gaps.append("The workflow needs exactly one trigger: what should start it?")
    if not any(s.kind == "action" and not (s.app is None and s.operation in PLACEHOLDER_OPERATIONS)
               for s in state.steps):
        gaps.append("The workflow has no action yet: what should it do?")
    for step in state.steps:
        for param in step.params:
            problem = reference_problem(state, step, param.value)
            if problem:
                gaps.append(f"{step.title} · {param.label}: {problem}")
    return gaps + structure_errors(state)
