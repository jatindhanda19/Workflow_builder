"""Workflow output: pydantic models plus the JSON Schema every generated workflow is validated against."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class WorkflowNode(BaseModel):
    id: str
    type: str
    name: str
    parameters: dict[str, Any] = Field(default_factory=dict)


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
