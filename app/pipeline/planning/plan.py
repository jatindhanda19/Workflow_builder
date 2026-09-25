"""Builds the plan: which nodes the workflow needs and which fields must be collected.

The plan is a pure function of the collected values and the intent, so it is
recomputed on every turn and follows any answer that changes what applies.
"""

from dataclasses import dataclass

from app.pipeline.planning.facts import derive_facts
from app.registry import NODE_TYPES, FieldDef, NodeType, When
from app.state.models import FieldValue

Context = dict[str, object]


@dataclass(frozen=True)
class PlannedNode:
    node: NodeType
    emitted: bool | None  # True: in the workflow, False: not, None: not decided yet


@dataclass(frozen=True)
class Plan:
    context: Context
    nodes: tuple[PlannedNode, ...]
    fields: tuple[FieldDef, ...]
    not_applicable: tuple[FieldDef, ...]

    def is_required(self, fdef: FieldDef) -> bool:
        return fdef.required and holds(fdef.required_when, self.context)

    def node(self, node_id: str) -> PlannedNode | None:
        return next((p for p in self.nodes if p.node.id == node_id), None)


def build_plan(values: dict[str, FieldValue], cardinality: str | None) -> Plan:
    context: Context = {**values, **derive_facts(values, cardinality), "intent.cardinality": cardinality}
    selected = sorted((n for n in NODE_TYPES if holds(n.conditions, context)), key=lambda n: n.stage)
    planned = tuple(PlannedNode(n, _emitted(n, context)) for n in selected)
    fields, not_applicable = [], []
    for item in selected:
        for fdef in item.fields:
            (fields if holds(fdef.applies_when, context) else not_applicable).append(fdef)
    ordered = sorted(fields, key=lambda fd: (fd.phase, _stage(fd, selected), _index(fd, selected)))
    return Plan(context, planned, tuple(ordered), tuple(not_applicable))


def holds(conditions: tuple[When, ...], context: Context) -> bool:
    return all(context.get(rule.key) in rule.values for rule in conditions)


def _emitted(node: NodeType, context: Context) -> bool | None:
    if node.category == "setup":
        return False
    if not node.emit_when:
        return True
    if holds(node.emit_when, context):
        return True
    undecided = any(rule.key not in context for rule in node.emit_when if not rule.key.startswith("fact."))
    return None if undecided else False


def _stage(fdef: FieldDef, nodes: list[NodeType]) -> int:
    return next(n.stage for n in nodes if n.id == fdef.node)


def _index(fdef: FieldDef, nodes: list[NodeType]) -> int:
    owner = next(n for n in nodes if n.id == fdef.node)
    return owner.fields.index(fdef)
