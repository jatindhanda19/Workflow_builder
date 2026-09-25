"""Builds a flow diagram deterministically from the workflow JSON (nodes + edges). The LLM never draws it.

Categories, subtitles and shapes come from the registry, looked up by node type.
Condition branches are labelled Yes/No, and a loop is drawn as a group with a
dashed "next item" edge back to the loop node.
"""

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from app.registry import WORKFLOW_NODES, Category, NodeType
from app.registry.display import summary_line

ICONS = {"trigger": "⚡", "logic": "🔀", "data": "🗂", "action": "📤"}
PLACEHOLDER = re.compile(r"\{(\w+)\}")


@dataclass(frozen=True)
class DiagramNode:
    id: str
    name: str
    type: str
    category: Category
    subtitle: str
    parameters: dict[str, Any]
    pending: bool


@dataclass(frozen=True)
class DiagramEdge:
    source: str
    target: str
    label: str | None = None
    dashed: bool = False


@dataclass(frozen=True)
class Diagram:
    nodes: list[DiagramNode]
    edges: list[DiagramEdge]
    loop: tuple[str, list[str]] | None = None


def build_diagram(workflow: dict[str, Any], pending: set[str] | frozenset[str] = frozenset()) -> Diagram:
    nodes = [_diagram_node(n, n["id"] in pending) for n in workflow["nodes"]]
    edges = [
        DiagramEdge(e["from"], e["to"], {"true": "Yes", "false": "No"}.get(e.get("branch") or ""))
        for e in workflow["edges"]
    ]
    loop = _loop_scope(workflow["nodes"])
    if loop and loop[1]:
        edges.append(DiagramEdge(loop[1][-1], loop[0], "next item", dashed=True))
    return Diagram(nodes=nodes, edges=edges, loop=loop)


def node_type_for(workflow_type: str) -> NodeType | None:
    return next((n for n, pattern in _type_patterns() if pattern.fullmatch(workflow_type)), None)


def category_for(workflow_type: str) -> Category:
    if workflow_type == "end":
        return "logic"
    if workflow_type == "pending_trigger":
        return "trigger"
    if workflow_type == "pending_action":
        return "action"
    node = node_type_for(workflow_type)
    return node.category if node else "logic"


def _diagram_node(node: dict[str, Any], pending: bool) -> DiagramNode:
    node_type = node_type_for(node["type"])
    subtitle = summary_line(node_type, node["parameters"]) if node_type else ""
    if subtitle and subtitle.lower() in node["name"].lower():
        subtitle = ""
    if pending and not subtitle:
        subtitle = "pending"
    return DiagramNode(
        id=node["id"], name=node["name"], type=node["type"], category=category_for(node["type"]),
        subtitle=subtitle, parameters=node["parameters"], pending=pending,
    )


def _loop_scope(nodes: list[dict[str, Any]]) -> tuple[str, list[str]] | None:
    loop = next((n["id"] for n in nodes if n["type"] == "loop"), None)
    if loop is None:
        return None
    return loop, [n["id"] for n in nodes if n["parameters"].get("for_each") == loop]


def _type_regex(node: NodeType) -> str:
    """"{provider}_trigger" becomes "(?:gmail|outlook|imap)_trigger", using the field's choices."""

    def alternatives(match: re.Match[str]) -> str:
        fdef = next((f for f in node.fields if f.name == match.group(1)), None)
        values = [c.value for c in fdef.choices] if fdef and fdef.choices else []
        return "(?:" + "|".join(map(re.escape, values)) + ")" if values else "[a-z0-9_]+"

    parts = PLACEHOLDER.split(node.workflow_type or "")
    return "".join(alternatives(PLACEHOLDER.match("{" + p + "}")) if i % 2 else re.escape(p) for i, p in enumerate(parts))  # type: ignore[arg-type]


@lru_cache
def _type_patterns() -> tuple[tuple[NodeType, re.Pattern[str]], ...]:
    patterns = []
    for node in WORKFLOW_NODES:
        if node.workflow_type:
            patterns.append((node, re.compile(_type_regex(node))))
        patterns.append((node, re.compile(re.escape(f"pending_{node.id}"))))
    return tuple(patterns)
