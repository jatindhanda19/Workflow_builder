from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.agent_schema import Ambiguity, Extraction, FieldValue
from app.schemas.workflow_schema import Workflow

class Message(BaseModel):
    role : Literal["user", "assistant"]
    content: str

class WorkflowState(BaseModel):
    messages: list[Message] = Field(default_factory=list)
    latest_user_message: str = ""
    original_request: str | None = None

    workflow_type: str | None = None
    collected: dict[str, FieldValue] = Field(default_factory=dict)
    required_fields: list[str] = Field(default_factory=list)
    missing_fields : list[str] = Field(default_factory=list)
    ambiguities: list[Ambiguity] = Field(default_factory=list)
    relevant_optional: list[str] = Field(default_factory=list)
    asked_optional:list[str] = Field(default_factory=list)
    changes: list[str] = Field(default_factory=list)

    pending_extraction: Extraction | None = None
    analysis_failed: bool = False
    llm_reports_complete: bool = False
    info_complete: bool = False

    next_question :str | None= None
    target_field: str | None = None

    status: Literal["collecting", "complete", "failed"] = "collecting"
    workflow: Workflow | None = None
    generation_attempts: int = 0
    generation_error: list[str] = Field(default_factory=list)

    