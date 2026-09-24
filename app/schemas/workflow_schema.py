from typing import Any

from pydantic import BaseModel, ConfigDict, Field

class WorkflowNode(BaseModel):
    id: str
    type: str
    config: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_trigger(self) -> bool:
        return self.type.endswith("_trigger")

class Connection(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source :str = Field(alias="from")
    target: str = Field(alias="to")

class Workflow(BaseModel):
    name:str
    nodes: list[WorkflowNode]
    Connections: list[Connection] = Field(default_factory=list)
    