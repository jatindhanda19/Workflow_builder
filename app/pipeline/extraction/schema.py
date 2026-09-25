from pydantic import BaseModel, Field


class ExtractedValue(BaseModel):
    field: str = Field(description="Full field key from the catalogue, e.g. sheet_trigger.file_name")
    value: str = Field(description="Only the normalised value, never the whole sentence")
    evidence: str = Field(description="The exact words from the latest user message that state this value")


class AmbiguityFlag(BaseModel):
    field: str = Field(description="Full field key the unclear phrase relates to")
    phrase: str = Field(description="Exact words from the latest user message that are unclear")
    reason: str = Field(description="Why the phrase cannot be used as a concrete value")
    interpretations: list[str] = Field(default_factory=list, description="Concrete readings the phrase could have")


class Extraction(BaseModel):
    values: list[ExtractedValue] = Field(default_factory=list)
    ambiguities: list[AmbiguityFlag] = Field(default_factory=list)
    is_new_request: bool = Field(
        default=False, description="True only if the user describes a different automation from the one being built"
    )


class PhrasedQuestion(BaseModel):
    question: str = Field(description="One short, friendly question")
