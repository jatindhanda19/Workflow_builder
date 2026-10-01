"""What the planner LLM returns each turn. Code merges, grounds and validates all of it."""

from typing import Literal

from pydantic import BaseModel, Field

StepKind = Literal["trigger", "action", "condition"]
ParamKind = Literal[
    "text", "long_text", "list", "email", "email_list", "url", "number", "time", "timezone", "choice", "channel",
]
MessageKind = Literal["build", "question", "new_request", "unclear", "other"]


class PlannedParam(BaseModel):
    name: str = Field(description="snake_case parameter name, e.g. channel, to, subject, spreadsheet")
    label: str = Field(description="Short human label, e.g. 'Slack channel'")
    description: str = Field(default="", description="What the parameter is for, one short phrase")
    kind: ParamKind = Field(default="text", description="Answer format")
    choices: list[str] = Field(default_factory=list, description="Allowed answers, only for kind=choice")
    required: bool = Field(default=True, description="True if the step cannot run without it")
    value: str | None = Field(default=None, description="Only a value the user stated or picked; null otherwise")
    evidence: str | None = Field(default=None, description="The exact user words that state the value")


class PlannedStep(BaseModel):
    id: str = Field(description="Stable snake_case id; keep the ids of the CURRENT DRAFT")
    kind: StepKind
    app: str | None = Field(default=None, description="App or built-in node name; null if the user has not said")
    app_evidence: str | None = Field(default=None, description="The exact user words that name or imply the app")
    operation: str = Field(description="What the step does, e.g. 'New order', 'Send message', 'Append row'")
    params: list[PlannedParam] = Field(default_factory=list)


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
