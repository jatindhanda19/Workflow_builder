from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from app.pipeline.extraction.schema import Extraction
from app.pipeline.generation.schema import Workflow
from app.pipeline.intent.schema import FollowUpDecision, IntentResult

FieldValue = str | list[str]
Mode = Literal["collecting", "ready", "post_generation"]
FieldStatus = Literal["filled", "ambiguous", "missing", "not_applicable"]
TargetKind = Literal["missing", "ambiguous", "conflict"]
LogEvent = Literal[
    "asked", "filled", "ambiguous", "conflict", "overwritten", "kept", "reopened", "derived", "ignored", "cleared",
]
PERSON_WORDS = frozenset({
    "client", "customer", "team", "manager", "user", "member", "employee", "vendor", "supplier", "lead",
    "contact", "subscriber", "student", "partner", "boss", "colleague", "staff",
})


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class Option(BaseModel):
    """One interpretation of an ambiguous answer and the values it sets (None clears a field)."""

    label: str
    assign: dict[str, FieldValue | None] = Field(default_factory=dict)
    keywords: list[str] = Field(default_factory=list)


class FieldEntry(BaseModel):
    key: str
    status: Literal["filled", "ambiguous"]
    value: FieldValue | None = None
    phrase: str | None = None
    reason: str | None = None
    question: str | None = None
    options: list[Option] = Field(default_factory=list)
    source: Literal["user", "derived"] = "user"


class Conflict(BaseModel):
    key: str
    old: FieldValue
    new: FieldValue


class LogEntry(BaseModel):
    turn: int
    field: str
    event: LogEvent
    detail: str = ""


class IntentState(BaseModel):
    direction: str | None = None
    entities: list[str] = Field(default_factory=list)
    data_sources: list[str] = Field(default_factory=list)
    cardinality: Literal["single", "multiple", "dynamic"] | None = None

    @property
    def main_entity(self) -> str | None:
        return next((e for e in self.entities if _singular(e) not in PERSON_WORDS), None)

    @property
    def recipient_entity(self) -> str | None:
        return next((e for e in self.entities if _singular(e) in PERSON_WORDS), None)


class WorkflowState(BaseModel):
    messages: list[Message] = Field(default_factory=list)
    latest_user_message: str = ""
    turn: int = 0
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    mode: Mode = "collecting"
    original_request: str | None = None
    intent: IntentState = Field(default_factory=IntentState)
    fields: dict[str, FieldEntry] = Field(default_factory=dict)
    conflicts: list[Conflict] = Field(default_factory=list)
    log: list[LogEntry] = Field(default_factory=list)

    target_field: str | None = None
    target_kind: TargetKind | None = None
    pending_new_request: str | None = None
    workflow: Workflow | None = None

    # Scratch values for the current turn, reset on every message.
    intent_result: IntentResult | None = None
    extraction: Extraction | None = None
    llm_failed: bool = False
    followup: FollowUpDecision | None = None
    edit_mode: bool = False
    changes: list[str] = Field(default_factory=list)
    acks: list[str] = Field(default_factory=list)
    reply: str | None = None
    reply_route: str | None = None

    def values(self) -> dict[str, FieldValue]:
        """Values of filled fields only. Ambiguous entries never count as values."""
        return {
            key: entry.value
            for key, entry in self.fields.items()
            if entry.status == "filled" and entry.value is not None
        }

    def is_filled(self, key: str) -> bool:
        entry = self.fields.get(key)
        return entry is not None and entry.status == "filled"

    def asked_details(self, key: str) -> list[str]:
        return [item.detail for item in self.log if item.field == key and item.event == "asked"]


def _singular(word: str) -> str:
    word = word.lower().strip()
    return word[:-1] if word.endswith("s") and not word.endswith("ss") else word
