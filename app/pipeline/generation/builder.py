"""Builds the n8n-style workflow JSON from the collected steps. No LLM: every value comes from the session."""

import re
from datetime import datetime, timezone
from typing import Any

from app.pipeline.generation.checks import validate_output
from app.pipeline.generation.schema import Edge, Workflow, WorkflowMetadata, WorkflowNode
from app.pipeline.validation import display, is_ready, links, open_items, structure_errors
from app.state.models import FieldValue, Step, WorkflowState

SUMMARY_PARAMS = 2


class GenerationError(RuntimeError):
    pass


def generate_workflow(state: WorkflowState) -> Workflow:
    if not is_ready(state):
        raise GenerationError(f"open items: {[i.key for i in open_items(state)]}; {structure_errors(state)}")
    nodes, edges = _graph(state.steps)
    trigger = state.steps[0]
    workflow = Workflow(
        metadata=WorkflowMetadata(
            name=state.name or f"{trigger.app} to {state.steps[-1].app}",
            trigger_summary=_summary(trigger),
            description=_describe(state.steps),
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
        nodes=nodes,
        edges=edges,
    )
    errors = validate_output(workflow, _grounded_values(state))
    if errors:
        raise GenerationError("; ".join(errors))
    return workflow


def matches_draft(workflow: Workflow | None, state: WorkflowState) -> bool:
    """The workflow was generated from the current steps, so it does not need regenerating."""
    if workflow is None:
        return False
    nodes, edges = _graph(state.steps)
    return workflow.nodes == nodes and workflow.edges == edges


def _graph(steps: list[Step]) -> tuple[list[WorkflowNode], list[Edge]]:
    """Nodes, and one edge per parent of each step (see validation.links). A condition outcome with no step
    leads to End."""
    nodes = [_node(step) for step in steps]
    parents = links(steps)
    edges = [Edge(source=p, target=s.id, branch=b) for s in steps[1:] for p, b in parents[s.id]]
    used = {(e.source, e.branch) for e in edges}
    to_end = [(s.id, b) for s in steps if s.kind == "condition" for b in ("true", "false") if (s.id, b) not in used]
    if to_end:
        nodes.append(WorkflowNode(id="end_1", type="end", name="End", parameters={"reason": "nothing to do"}))
        edges += [Edge(source=source, target="end_1", branch=branch) for source, branch in to_end]
    return nodes, edges


def _describe(steps: list[Step]) -> str:
    """E.g. "Square: New order → order.total > 50000? → yes: Gmail: Send email / no: MySQL: Insert row"."""
    by_id = {s.id: s for s in steps}
    children: dict[str, list[tuple[str, str | None]]] = {s.id: [] for s in steps}
    for child, parents in links(steps).items():
        for parent, branch in parents:
            children[parent].append((child, branch))
    seen: set[str] = set()

    def walk(step_id: str) -> str:
        step = by_id[step_id]
        if step_id in seen:  # a step after both outcomes is described once
            return step.title
        seen.add(step_id)
        kids = children[step_id]
        if step.kind == "condition":
            outcome = {branch: walk(child) for child, branch in kids}
            return f"{step.title} → yes: {outcome.get('true', 'end')} / no: {outcome.get('false', 'end')}"
        return " → ".join([step.title, " + ".join(walk(child) for child, _ in kids)]) if kids else step.title

    return walk(steps[0].id)


def _node(step: Step) -> WorkflowNode:
    app = _slug(step.app or "app")
    if step.kind == "trigger":
        node_type = f"{app}_trigger"
    elif step.kind == "condition":
        node_type = "condition"
    else:
        node_type = f"{app}_{_slug(step.operation)}".strip("_")
    parameters: dict[str, Any] = {"app": step.app, "operation": step.operation}
    parameters |= {p.name: p.value for p in step.params if p.value is not None}
    return WorkflowNode(id=step.id, type=node_type, name=step.title, parameters=parameters)


def _summary(step: Step) -> str:
    details = [f"{p.label}: {display(p, p.value)}" for p in step.params if p.value is not None][:SUMMARY_PARAMS]
    return " · ".join([step.title, *details])


def _grounded_values(state: WorkflowState) -> dict[str, FieldValue]:
    """Values that must appear in the output: every collected parameter."""
    return {f"{s.id}.{p.name}": p.value for s in state.steps for p in s.params if p.value is not None}


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "step"
    return slug if slug[0].isalpha() else f"n_{slug}"
