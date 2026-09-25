"""Checks every generated workflow must pass: JSON Schema, graph structure and grounding."""

import json
import re
from collections import defaultdict

from jsonschema import Draft202012Validator

from app.pipeline.generation.schema import WORKFLOW_JSON_SCHEMA, Workflow
from app.state.models import FieldValue

_SCHEMA = Draft202012Validator(WORKFLOW_JSON_SCHEMA)


def validate_output(workflow: Workflow, grounded_values: dict[str, FieldValue]) -> list[str]:
    data = workflow.model_dump(by_alias=True)
    errors = [f"schema: {e.message} at {'/'.join(map(str, e.path))}" for e in _SCHEMA.iter_errors(data)]
    if errors:
        return errors
    return _graph_errors(workflow) + _grounding_errors(data, grounded_values)


def _graph_errors(workflow: Workflow) -> list[str]:
    ids = [n.id for n in workflow.nodes]
    errors = [f"duplicate node id {i}" for i in sorted(set(ids)) if ids.count(i) > 1]
    triggers = [n for n in workflow.nodes if n.type.endswith("_trigger")]
    if len(triggers) != 1 or workflow.nodes[0] is not triggers[0]:
        return errors + ["the workflow needs exactly one trigger, as its first node"]
    children: dict[str, list[str]] = defaultdict(list)
    branches: dict[str, list[str | None]] = defaultdict(list)
    for edge in workflow.edges:
        if edge.source not in ids or edge.target not in ids:
            errors.append(f"edge {edge.source}->{edge.target} references an unknown node")
        children[edge.source].append(edge.target)
        branches[edge.source].append(edge.branch)
    for node in workflow.nodes:
        found = sorted(b or "" for b in branches[node.id])
        if node.type == "condition" and found != ["false", "true"]:
            errors.append(f"condition {node.id} needs one true and one false branch")
        if node.type != "condition" and any(branches[node.id]):
            errors.append(f"only conditions may branch ({node.id})")
    reachable = _reachable(triggers[0].id, children)
    errors += [f"{i} is not reachable from the trigger" for i in ids if i not in reachable]
    if _has_cycle(ids, children):
        errors.append("the workflow contains a cycle")
    return errors


def _grounding_errors(data: dict, values: dict[str, FieldValue]) -> list[str]:
    haystack = _squash(json.dumps(data, ensure_ascii=False))
    errors = []
    for key, value in values.items():
        for item in value if isinstance(value, list) else [value]:
            if _squash(item.split("=")[-1]) not in haystack:
                errors.append(f"'{item}' ({key}) is missing from the workflow")
    return errors


def _reachable(start: str, children: dict[str, list[str]]) -> set[str]:
    seen, stack = set(), [start]
    while stack:
        current = stack.pop()
        if current not in seen:
            seen.add(current)
            stack.extend(children[current])
    return seen


def _has_cycle(ids: list[str], children: dict[str, list[str]]) -> bool:
    state: dict[str, int] = {}

    def visit(node: str) -> bool:
        if state.get(node) == 1:
            return True
        if state.get(node) == 2:
            return False
        state[node] = 1
        found = any(visit(child) for child in children[node])
        state[node] = 2
        return found

    return any(visit(i) for i in ids)


def _squash(text: str) -> str:
    return re.sub(r"[^\w₹$€£]", "", text.lower()).replace("_", "")
