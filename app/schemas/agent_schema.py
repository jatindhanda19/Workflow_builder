from pydantic import BaseModel, Field
FieldValue = str | list[str]

class ExtractedField(BaseModel):
    key: str = Field(description="Field key exactly as listed in the template")
    value: str = Field(description="Value the user explicitly stated")

class Ambiguity(BaseModel):
    field: str = Field(description="Field key the vague phrase relates to")
    phrase: str = Field(description="Exact words from the user's message that are unclear")
    reason: str = Field(description="why the phrase cannot be used as a concrete value")

class Extraction(BaseModel):
    Workflow_type: str| None = Field(default=None, description="Template id, or null if unknown")
    fields: list[ExtractedField] = Field(default_factory=list)
    ambiguities: list[Ambiguity] = Field(default_factory=list)
    relevant_optional: list[str] = Field(
        default_factory=list,
        description="Optional field keys the user touched on without giving a usable value",
    )
    looks_complete: bool = Field(
        default= False,
        description="Opinion on whether enough information exists; verified independently",
    )

class AgentDecision(BaseModel):
    target_field: str = Field(description="One of the candidate field keys")
    question: str  = Field(description="A single clarification question for the user")
    rational:str = ""
    