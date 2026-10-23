from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

_OLD_CONDITION_KEYS = ("field", "operator", "value")


class Condition(BaseModel):
    """One comparison: a field of earlier data, compared with a value (none for is_empty / is_not_empty)."""

    field: str
    operator: str
    value: Any = None


class ConditionGroup(BaseModel):
    """What an If node checks: its comparisons, and whether all must hold ("and") or one is enough ("or")."""

    conditions: list[Condition]
    combinator: Literal["and", "or"] = "and"

    @classmethod
    def from_parameters(cls, parameters: dict[str, Any]) -> "ConditionGroup":
        migrated = migrate_condition_parameters(parameters)
        return cls(conditions=migrated.get("conditions") or [], combinator=migrated.get("combinator") or "and")


def migrate_condition_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    """Workflows generated before AND/OR conditions held one comparison as field / operator / value: that is a
    list of one comparison combined with "and". Parameters already in the new shape are returned unchanged."""
    if "conditions" in parameters or "field" not in parameters:
        return parameters
    rest = {k: v for k, v in parameters.items() if k not in _OLD_CONDITION_KEYS}
    condition = {key: parameters.get(key) for key in _OLD_CONDITION_KEYS}
    return {**rest, "conditions": [condition], "combinator": "and"}


class WorkflowNode(BaseModel):
    id: str
    type: str
    name: str
    parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _migrate_old_condition(cls, data: Any) -> Any:
        """Stored workflows are upgraded when they are read, so every condition node has the new shape."""
        if isinstance(data, dict) and data.get("type") == "condition" and isinstance(data.get("parameters"), dict):
            return {**data, "parameters": migrate_condition_parameters(data["parameters"])}
        return data


class Edge(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source: str = Field(alias="from")
    target: str = Field(alias="to")
    branch: Literal["true", "false"] | None = None


class WorkflowMetadata(BaseModel):
    name: str
    trigger_summary: str
    description: str
    created_at: str


class Workflow(BaseModel):
    metadata: WorkflowMetadata
    nodes: list[WorkflowNode]
    edges: list[Edge] = Field(default_factory=list)

    @property
    def name(self) -> str:
        return self.metadata.name


WORKFLOW_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["metadata", "nodes", "edges"],
    "additionalProperties": False,
    "properties": {
        "metadata": {
            "type": "object",
            "required": ["name", "trigger_summary", "description", "created_at"],
            "additionalProperties": False,
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "trigger_summary": {"type": "string", "minLength": 1},
                "description": {"type": "string"},
                "created_at": {"type": "string", "format": "date-time"},
            },
        },
        "nodes": {
            "type": "array",
            "minItems": 2,
            "items": {
                "type": "object",
                "required": ["id", "type", "name", "parameters"],
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
                    "type": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
                    "name": {"type": "string", "minLength": 1},
                    "parameters": {"type": "object"},
                },
            },
        },
        "edges": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["from", "to", "branch"],
                "additionalProperties": False,
                "properties": {
                    "from": {"type": "string"},
                    "to": {"type": "string"},
                    "branch": {"enum": [None, "true", "false"]},
                },
            },
        },
    },
}
