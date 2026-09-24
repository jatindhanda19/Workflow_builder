from dataclasses import dataclass
from typing import Literal

VAGUE_PEOPLE = frozenset({
    "team", "the team", "our team", "my team", "everyone", "everybody", "the group",
    "people", "them", "someone", "anyone", "stakeholders", "the right people",
    "relevant people", "the client", "clients",
})
VAGUE_DETAILS = frozenset({
    "details", "info", "information", "everything", "all", "all details", "all info",
    "the details", "lead details", "lead info", "relevant details", "relevant info",
    "basic info", "basic details", "data", "the data", "stuff",
})
VAGUE_CONTENT = VAGUE_DETAILS | frozenset({
    "a message", "message", "a notification", "notification", "an update", "update",
    "something", "text",
})
VAGUE_SOURCES = frozenset({
    "the report", "report", "a report", "the data", "data", "it", "the form", "a form",
    "form", "the source", "source", "leads", "new leads", "the leads", "our leads",
    "the app", "the system",
})
VAGUE_TRIGGERS = frozenset({
    "something happens", "an event", "event", "when something happens", "trigger",
    "a trigger", "when needed",
})
VAGUE_CHANNELS = frozenset({"a message", "message", "notification", "a notification", "somehow"})
VAGUE_TIMEZONES = frozenset({
    "my timezone", "my time zone", "our timezone", "our time zone", "local", "local time",
    "local timezone", "my time",
})

TIME_PATTERN = r"(?:(?:[01]?\d|2[0-3])(?::[0-5]\d)?(?:am|pm)?|noon|midnight)"
DAY_OF_MONTH_PATTERN = r"(?:[1-9]|[12]\d|3[01])(?:st|nd|rd|th)?|last"
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


@dataclass(frozen=True)
class FieldSpec:
    key: str
    description: str
    question: str
    required: bool = True
    kind: Literal["text", "list"] = "text"
    choices: tuple[str, ...] = ()
    pattern: str | None = None
    pattern_hint: str = ""
    vague: frozenset[str] = frozenset()
    depends_on: str | None = None
    only_if: tuple[str, tuple[str, ...]] | None = None
    invalidated_by: tuple[str, ...] = ()
    blocking: bool = False
    verify_in_output: bool = False


@dataclass(frozen=True)
class WorkflowTemplate:
    id: str
    description: str
    fields: tuple[FieldSpec, ...]

    def field(self, key: str) -> FieldSpec | None:
        return next((spec for spec in self.fields if spec.key == key), None)

EMAIL_NOTIFICATION = WorkflowTemplate(
    id="email_notification",
    description="Sends an email when a specific event happens",
    fields=(
        FieldSpec(
            "trigger_event",
            "The event that should trigger the email, e.g. a form is submitted or a row is added",
            "What event should trigger the email?",
            vague=VAGUE_TRIGGERS,
            blocking=True,
        ),
        FieldSpec(
            "trigger_source",
            "The specific form, app, sheet or system where that event happens",
            "Where does that event happen (which form, app or system)?",
            depends_on="trigger_event",
            vague=VAGUE_SOURCES,
            verify_in_output=True,
        ),
        FieldSpec(
            "recipient",
            "Who receives the email: an address, a named person or a named group",
            "Who should receive the email?",
            vague=VAGUE_PEOPLE,
            verify_in_output=True,
        ),
        FieldSpec("subject", "The email subject line", "What should the email subject be?"),
        FieldSpec(
            "message_content",
            "What the email should say or contain",
            "What should the email say?",
            vague=VAGUE_CONTENT,
        ),
        FieldSpec("cc", "People copied on the email", "Who should be copied?", required=False),
    ),
)

SCHEDULED_REPORT = WorkflowTemplate(
    id="scheduled_report",
    description="Sends a report on a recurring schedule",
    fields=(
        FieldSpec(
            "report_source",
            "The specific report or data source that produces the report",
            "Which report should be sent, and where does it come from?",
            vague=VAGUE_SOURCES,
            blocking=True,
            verify_in_output=True,
        ),
        FieldSpec(
            "frequency",
            "How often the report is sent",
            "How often should the report be sent?",
            choices=("daily", "weekdays", "weekly", "monthly"),
        ),
        FieldSpec(
            "run_time",
            "The exact time of day the report is sent",
            "At what exact time should it be sent?",
            pattern=TIME_PATTERN,
            pattern_hint="a specific time such as 9:00 AM or 14:30",
            depends_on="frequency",
        ),
        FieldSpec(
            "day_of_week",
            "The weekday the weekly report is sent",
            "On which day of the week should it be sent?",
            choices=WEEKDAYS,
            depends_on="frequency",
            only_if=("frequency", ("weekly",)),
        ),
        FieldSpec(
            "day_of_month",
            "The day of the month the monthly report is sent",
            "On which day of the month should it be sent?",
            pattern=DAY_OF_MONTH_PATTERN,
            pattern_hint="a day number from 1 to 31, or 'last'",
            depends_on="frequency",
            only_if=("frequency", ("monthly",)),
        ),
        FieldSpec(
            "timezone",
            "The timezone the schedule follows",
            "Which timezone should the schedule follow?",
            vague=VAGUE_TIMEZONES,
        ),
        FieldSpec(
            "delivery_channel",
            "How the report is delivered, e.g. email or Slack",
            "How should the report be delivered?",
            vague=VAGUE_CHANNELS,
            verify_in_output=True,
        ),
        FieldSpec(
            "recipient",
            "Who receives the report on that channel: an address, a named person, group or channel",
            "Who exactly should receive it?",
            depends_on="delivery_channel",
            invalidated_by=("delivery_channel",),
            vague=VAGUE_PEOPLE,
            verify_in_output=True,
        ),
        FieldSpec(
            "report_format",
            "The format of the report, e.g. PDF, CSV or inline text",
            "What format should the report have?",
            required=False,
        ),
    ),
)

LEAD_NOTIFICATION = WorkflowTemplate(
    id="lead_notification",
    description="Notifies people when a new lead arrives",
    fields=(
        FieldSpec(
            "lead_source",
            "Where new leads come from, e.g. a named web form, CRM or ad platform",
            "Which source should generate the leads?",
            vague=VAGUE_SOURCES,
            blocking=True,
            verify_in_output=True,
        ),
        FieldSpec(
            "notification_channel",
            "How the people are notified, e.g. email, Slack or SMS",
            "How should the notification be sent?",
            vague=VAGUE_CHANNELS,
            verify_in_output=True,
        ),
        FieldSpec(
            "recipient",
            "Who is notified on that channel: an address, a named person, group or channel",
            "Who exactly should be notified?",
            depends_on="notification_channel",
            invalidated_by=("notification_channel",),
            vague=VAGUE_PEOPLE,
            verify_in_output=True,
        ),
        FieldSpec(
            "lead_fields",
            "The lead details to include in the notification",
            "Which lead details should the notification include?",
            kind="list",
            vague=VAGUE_DETAILS,
            verify_in_output=True,
        ),
        FieldSpec(
            "lead_filter",
            "A condition a lead must meet before anyone is notified",
            "Should only certain leads trigger the notification?",
            required=False,
        ),
    ),
)

GENERIC_TRIGGER_ACTION = WorkflowTemplate(
    id="generic_trigger_action",
    description="Any other automation shaped like: when X happens, do Y",
    fields=(
        FieldSpec(
            "trigger_event",
            "The event that starts the workflow",
            "What event should start this workflow?",
            vague=VAGUE_TRIGGERS,
            blocking=True,
        ),
        FieldSpec(
            "trigger_source",
            "The specific app, form, sheet or system where that event happens",
            "Where does that event happen?",
            depends_on="trigger_event",
            vague=VAGUE_SOURCES,
            verify_in_output=True,
        ),
        FieldSpec(
            "action_type",
            "What the workflow should do, e.g. send a message, create a record, update a row",
            "What should the workflow do when that happens?",
        ),
        FieldSpec(
            "action_target",
            "The app, recipient or system the action is applied to",
            "Where or to whom should that action be applied?",
            depends_on="action_type",
            invalidated_by=("action_type",),
            vague=VAGUE_SOURCES | VAGUE_PEOPLE,
            verify_in_output=True,
        ),
        FieldSpec(
            "action_details",
            "The content or data the action should use",
            "What content or data should the action use?",
            vague=VAGUE_CONTENT,
        ),
        FieldSpec(
            "conditions",
            "A condition that must hold before the action runs",
            "Should the action only run under certain conditions?",
            required=False,
        ),
    ),
)

TEMPLATES: dict[str, WorkflowTemplate] = {
    template.id: template
    for template in (
        EMAIL_NOTIFICATION,
        SCHEDULED_REPORT,
        LEAD_NOTIFICATION,
        GENERIC_TRIGGER_ACTION,
    )
}

def get_template(workflow_type: str | None) -> WorkflowTemplate | None:
    return TEMPLATES.get(workflow_type) if workflow_type else None

def describe_templates() -> str:
    return "\n\n".join(_describe(template) for template in TEMPLATES.values())

def _describe(template: WorkflowTemplate) -> str:
    lines = [f"{template.id}: {template.description}"]
    for spec in template.fields:
        flags = ["required" if spec.required else "optional"]
        if spec.kind == "list":
            flags.append("list")
        if spec.choices:
            flags.append("one of: " + ", ".join(spec.choices))
        if spec.only_if:
            key, values = spec.only_if
            flags.append(f"only when {key} is {' or '.join(values)}")
        lines.append(f"  - {spec.key} ({'; '.join(flags)}): {spec.description}")
    return "\n".join(lines)

