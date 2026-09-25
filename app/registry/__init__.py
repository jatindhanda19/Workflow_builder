"""Node-type and field registry (data only)."""

from app.registry.model import Category, Choice, FieldDef, FieldKind, NodeType, Phase, When
from app.registry.nodes import NO, NODE_TYPES, SHEET_PLATFORMS, WORKFLOW_NODES, YES

NODES: dict[str, NodeType] = {n.id: n for n in NODE_TYPES}
FIELDS: dict[str, FieldDef] = {fd.key: fd for n in NODE_TYPES for fd in n.fields}
NODE_ORDER: dict[str, int] = {n.id: i for i, n in enumerate(NODE_TYPES)}


def get_field(key: str) -> FieldDef | None:
    return FIELDS.get(key)


def selected_node_for(selector_key: str, value: object) -> NodeType | None:
    """The node picked by a selector answer, e.g. trigger.kind = new_email → email_trigger."""
    return next(
        (n for n in WORKFLOW_NODES if n.selector and n.selector[0] == selector_key and n.selector[1].value == value),
        None,
    )


__all__ = [
    "Category", "Choice", "FIELDS", "FieldDef", "FieldKind", "NODES", "NODE_ORDER", "NODE_TYPES", "NO",
    "NodeType", "Phase", "SHEET_PLATFORMS", "WORKFLOW_NODES", "When", "YES", "get_field", "selected_node_for",
]
