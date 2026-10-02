from typing import Literal

from pydantic import BaseModel, Field, field_validator

StepKind = Literal["trigger", "action", "condition"]
Branch = Literal["true", "false"]
ParamKind = Literal[
    "text", "long_text", "list", "email", "email_list", "url", "number", "time", "timezone", "choice", "channel",
    "field", "operator",
]
MessageKind = Literal["build", "question", "new_request", "unclear", "other"]


class PlannedParam(BaseModel):
    name: str = Field(description="snake_case parameter name, e.g. channel, to, subject, spreadsheet")
    label: str = Field(description="Short human label, e.g. 'Slack channel'")
    description: str = Field(default="", description="What the parameter is for, one short phrase")
    kind: ParamKind = Field(default="text", description="Answer format")
    choices: list[str] = Field(default_factory=list, description="Allowed answers, only for kind=choice")
    required: bool = Field(default=True, description="True if the step cannot run without it")
    value: str | None = Field(default=None, description=(
        "Only a value the user stated or picked; null otherwise. Always a string: a list is comma-separated"))
    evidence: str | None = Field(default=None, description="The exact user words that state the value")

    @field_validator("value", "evidence", mode="before")
    @classmethod
    def _as_text(cls, value: object) -> object:
        # Models sometimes write 890 for "890" or ["id", "total"] for "id, total". It is the same answer, still
        # grounded and validated in code (validation reads a list from comma-separated text anyway).
        if isinstance(value, list) and all(isinstance(v, (str, int, float)) for v in value):
            return ", ".join(str(v) for v in value) or None
        return str(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else value


class PlannedStep(BaseModel):
    id: str = Field(description="Stable snake_case id; keep the ids of the CURRENT DRAFT")
    kind: StepKind
    app: str | None = Field(default=None, description="App or built-in node name; null if the user has not said")
    app_evidence: str | None = Field(default=None, description="The exact user words that name or imply the app")
    operation: str = Field(description="What the step does, e.g. 'New order', 'Send message', 'Append row'")
    after: list[str] = Field(
        default_factory=list, description="Ids of the earlier steps this step follows; empty = the step just before it")
    branch: Branch | None = Field(
        default=None, description="When it follows a condition: true (condition met) or false (otherwise)")
    params: list[PlannedParam] = Field(default_factory=list)

    @field_validator("after", mode="before")
    @classmethod
    def _one_id_as_list(cls, value: object) -> object:
        return [value] if isinstance(value, str) else value

    @field_validator("branch", mode="before")
    @classmethod
    def _bool_as_branch(cls, value: object) -> object:
        return str(value).lower() if isinstance(value, bool) else value


class NextQuestion(BaseModel):
    target: str = Field(description="'<step id>.<param name>', '<step id>.app' for a missing app, or 'clarify'")
    question: str = Field(description="One short question, without the options")
    options: list[str] = Field(default_factory=list, description="2-5 likely answers, most likely first")


class TurnPlan(BaseModel):
    message_kind: MessageKind = Field(
        description="build, question about the workflow, new_request, unclear (several readings) or other")
    answer: str | None = Field(default=None, description="For message_kind=question: the answer from the draft")
    workflow_name: str | None = Field(default=None, description="Short title of the automation")
    steps: list[PlannedStep] = Field(default_factory=list, description="The whole workflow, trigger first")
    next_question: NextQuestion | None = Field(default=None, description="The best next question, if anything is missing")


