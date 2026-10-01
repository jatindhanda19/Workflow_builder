"""Builds the n8n-style workflow JSON from the collected steps. No LLM: every value comes from the session."""

import re
from datetime import datetime, timezone
from typing import Any

from app.pipeline.generation.checks import validate_output
from app.pipeline.generation.schema import Edge, Workflow, WorkflowMetadata, WorkflowNode
from app.pipeline.validation import display, is_ready, open_items
from app.state.models import FieldValue, Step, WorkflowState

SUMMARY_PARAMS = 2


class GenerationError(RuntimeError):
    pass


def generate_workflow(state: WorkflowState) -> Workflow:
    if not is_ready(state):
        raise GenerationError(f"open items: {[i.key for i in open_items(state)]}")
    if state.steps[-1].kind == "condition":
        raise GenerationError("a condition must be followed by a step")
    nodes = [_node(step) for step in state.steps]
    edges: list[Edge] = []
    branch: str | None = None
    for previous, node in zip(nodes, nodes[1:]):
        edges.append(Edge(source=previous.id, target=node.id, branch=branch))
        branch = "true" if node.type == "condition" else None
    conditions = [n for n in nodes if n.type == "condition"]
    if conditions:
        end = WorkflowNode(id="end_1", type="end", name="End", parameters={"reason": "condition not met"})
        nodes.append(end)
        edges += [Edge(source=c.id, target=end.id, branch="false") for c in conditions]
    trigger = state.steps[0]
    workflow = Workflow(
        metadata=WorkflowMetadata(
            name=state.name or f"{trigger.app} to {state.steps[-1].app}",
            trigger_summary=_summary(trigger),
            description=" → ".join(n.name for n in nodes if n.type != "end"),
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
    return workflow is not None and [n for n in workflow.nodes if n.type != "end"] == [_node(s) for s in state.steps]


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
