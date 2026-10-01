from typing import Literal

from pydantic import BaseModel, Field

from app.pipeline.generation.schema import Workflow
from app.pipeline.planner_schema import ParamKind, StepKind, TurnPlan

FieldValue = str | list[str]
Mode = Literal["collecting", "ready", "post_generation"]
LogEvent = Literal["asked", "filled", "overwritten", "rejected", "ignored", "cleared"]
APP = "app"  # pseudo-parameter: the app of a step that is not decided yet


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    options: list[str] = Field(default_factory=list)  # the MCQ options shown with this message


class QuestionOption(BaseModel):
    label: str


class Question(BaseModel):
    """The multiple-choice question shown to the user. `target` is "<step id>.<parameter>"."""

    target: str | None = None
    step: str | None = None  # which step of the automation the question is about
    text: str
    options: list[QuestionOption] = Field(default_factory=list)
    allow_custom: bool = True


class Param(BaseModel):
    name: str
    label: str
    description: str = ""
    kind: ParamKind = "text"
    choices: list[str] = Field(default_factory=list)
    required: bool = True
    value: FieldValue | None = None
    note: str | None = None  # why the last answer was rejected


class Step(BaseModel):
    """One n8n-style node: a trigger, an action in any app, or a condition."""

    id: str
    kind: StepKind
    app: str | None = None  # None until the user names it
    operation: str
    params: list[Param] = Field(default_factory=list)

    @property
    def title(self) -> str:
        return f"{self.app}: {self.operation}" if self.app else self.operation

    def param(self, name: str) -> Param | None:
        return next((p for p in self.params if p.name == name), None)


class LogEntry(BaseModel):
    turn: int
    field: str
    event: LogEvent
    detail: str = ""


class WorkflowState(BaseModel):
    messages: list[Message] = Field(default_factory=list)
    latest_user_message: str = ""
    turn: int = 0

    mode: Mode = "collecting"
    name: str | None = None
    steps: list[Step] = Field(default_factory=list)
    log: list[LogEntry] = Field(default_factory=list)

    question: Question | None = None
    target: str | None = None
    pending_new_request: str | None = None
    workflow: Workflow | None = None

    # Scratch values for the current turn, reset on every message.
    plan: TurnPlan | None = None
    picked: str | None = None
    llm_failed: bool = False
    changes: list[str] = Field(default_factory=list)
    reply: str | None = None
    reply_route: str | None = None

    def step(self, step_id: str) -> Step | None:
        return next((s for s in self.steps if s.id == step_id), None)

    def user_text(self) -> str:
        return "\n".join(m.content for m in self.messages if m.role == "user")

    def asked_count(self, key: str) -> int:
        return sum(1 for item in self.log if item.field == key and item.event == "asked")

    def has_answers(self) -> bool:
        return any(s.app for s in self.steps) or any(p.value is not None for s in self.steps for p in s.params)
