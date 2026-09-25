"""Strict JSON contracts for intent classification and post-generation follow-ups."""

from typing import Literal

from pydantic import BaseModel, Field

GoalAction = Literal["notify", "send_document", "report", "sync_data", "backup", "post_message"]
Direction = Literal["inbound_event_alert", "outbound_send", "data_sync", "scheduled_report"]
TriggerHint = Literal["schedule", "sheet_event", "new_email", "new_file", "form_submission", "manual"]
ChannelHint = Literal["email", "slack"]

class IntentResult(BaseModel):
    goal_action: GoalAction | None = Field(
        default=None, description="What the user wants to achieve; null if the message does not say"
    )
    goal_evidence: str | None = Field(default=None, description="Exact words that state the goal")
    direction: Direction | None = None
    entities: list[str] = Field(
        default_factory=list, description="Singular nouns the workflow is about, e.g. invoice, client, report"
    )
    data_sources: list[str] = Field(default_factory=list, description="Apps or places the user mentioned")
    recipient_cardinality: Literal["single", "multiple", "dynamic"] | None = Field(
        default=None, description="single person, a fixed group, or one per record (e.g. each client)"
    )
    trigger_hint: TriggerHint | None = Field(
        default=None, description="What starts the workflow, only if the user said it"
    )
    trigger_evidence: str | None = Field(default=None, description="Exact words that reveal the trigger")
    channel_hint: ChannelHint | None = Field(
        default=None, description="How the result is delivered, only if the user said it (e.g. 'send an email')"
    )
    channel_evidence: str | None = Field(default=None, description="Exact words that reveal the channel")


class FollowUpDecision(BaseModel):
    kind: Literal["question", "edit", "new_request", "other"] = Field(
        description="question about the workflow, edit of the workflow, a new automation, or other"
    )
    answer: str | None = Field(
        default=None, description="For kind=question: the answer, using only the given workflow state"
    )
    fields: list[str] = Field(default_factory=list, description="For kind=edit: field keys the user wants to change")
    value: str | None = Field(
        default=None, description="For kind=edit with a single field: the new value, exactly as the user gave it"
    )
