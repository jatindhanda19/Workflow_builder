from typing import Literal

from pydantic import BaseModel, Field

from app.pipeline.generation.schema import Workflow
from app.pipeline.normalize import OPERATOR_SYMBOLS, UNARY_OPERATORS
from app.pipeline.planner_schema import Branch, ParamKind, StepKind, TurnPlan

FieldValue = str | list[str]
Mode = Literal["collecting", "ready", "post_generation"]
LogEvent = Literal["asked", "filled", "overwritten", "rejected", "ignored", "cleared"]
APP = "app"  # pseudo-parameter: the app of a step that is not decided yet


PreviousReason = Literal["edited", "new_workflow"]


class PlanRow(BaseModel):
    role: Literal["When", "If", "Then"]
    label: str  # "Gmail: Send email", "New rows found", "Send message (app to choose)"
    branch: Literal["yes", "no"] | None = None  # the condition outcome this step runs on


class ReplyParts(BaseModel):
    """An assistant reply in pieces, for a UI that lays them out itself. `Message.content` is the same reply
    as one Markdown string."""

    saved: str | None = None  # what this turn stored, e.g. "Time: 6:00 PM"
    lead: str | None = None  # text above the plan or question
    plan: list[PlanRow] = Field(default_factory=list)
    context: str | None = None  # "Step 1 of 4 · Trigger: Schedule: Every weekday"
    note: str | None = None  # why the last answer was rejected
    text: str = ""  # the question, or the main sentence
    more: str | None = None  # secondary text under `text`


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    options: list[str] = Field(default_factory=list)  # the MCQ options shown with this message
    parts: ReplyParts | None = None  # assistant messages only


class QuestionOption(BaseModel):
    label: str


class Question(BaseModel):
    """The multiple-choice question shown to the user. `target` is "<step id>.<parameter>"."""

    target: str | None = None
    step: str | None = None  # which step of the automation the question is about
    text: str
    note: str | None = None  # why the last answer was rejected, or that this was asked before
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
    guesses: list[str] = Field(default_factory=list)  # what the rejected answer may have meant ("6:00 PM")


class Step(BaseModel):
    """One n8n-style node: a trigger, an action in any app, or a condition."""

    id: str
    kind: StepKind
    app: str | None = None  # None until the user names it
    operation: str
    after: list[str] = Field(default_factory=list)  # earlier steps this one follows; empty: the step just before it
    branch: Branch | None = None  # the outcome of the condition it follows: true / false
    params: list[Param] = Field(default_factory=list)

    @property
    def title(self) -> str:
        if self.kind == "condition":
            # "order.total > 50000?" or "customer.email is empty?" once known, rather than the node name "If: If".
            field, operator, value = (self.param(n) for n in ("field", "operator", "value"))
            if field and operator and None not in (field.value, operator.value):
                symbol = OPERATOR_SYMBOLS.get(str(operator.value), operator.value)
                if operator.value in UNARY_OPERATORS:
                    return f"{field.value} {symbol}?"
                if value and value.value is not None:
                    return f"{field.value} {symbol} {value.value}?"
            return self.operation if self.operation.lower() not in ("if", "condition") else "Check condition"
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
    previous_workflows: list[Workflow] = Field(default_factory=list)  # generated earlier in this session, oldest first
    # Why each previous workflow was kept, same order: an older version of an edited one, or one replaced by a new
    # request. A separate list, so sessions saved before it existed still load.
    previous_reasons: list[PreviousReason] = Field(default_factory=list)

    # Scratch values for the current turn, reset on every message.
    plan: TurnPlan | None = None
    picked: str | None = None
    llm_failed: bool = False
    changes: list[str] = Field(default_factory=list)
    reply: str | None = None
    reply_parts: ReplyParts | None = None  # the same reply in pieces; None: just `reply` as text
    reply_plan: list[PlanRow] | None = None  # set when `reply` is the plan text
    reply_route: str | None = None

    def step(self, step_id: str) -> Step | None:
        return next((s for s in self.steps if s.id == step_id), None)

    def user_text(self) -> str:
        return "\n".join(m.content for m in self.messages if m.role == "user")

    def asked_count(self, key: str) -> int:
        return sum(1 for item in self.log if item.field == key and item.event == "asked")

    def has_answers(self) -> bool:
        return any(s.app for s in self.steps) or any(p.value is not None for s in self.steps for p in s.params)
