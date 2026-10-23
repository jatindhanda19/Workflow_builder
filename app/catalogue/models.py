"""The shape of a catalogue entry: one operation of one app, declared once and used by the planner, the
validator, the runtime and the n8n exporter."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from app.pipeline.planner_schema import ParamKind

Kind = Literal["trigger", "action", "logic"]
Settings = Mapping[str, Any]  # setting name → value, as collected in the chat


@dataclass(frozen=True)
class Setting:
    """One thing the user configures. `type` is a parameter kind the app already validates (time, email, …)."""

    name: str
    type: ParamKind
    label: str
    description: str
    required: bool = True
    choices: tuple[str, ...] = ()  # the only allowed values, for type "choice"
    aliases: tuple[str, ...] = ()  # other names a planner may give it ("text" for a message)


@dataclass(frozen=True)
class OutputField:
    """A field the step produces, which later steps can reference as {{step_id.<name>}}."""

    name: str
    description: str
    n8n_path: str  # where n8n puts it in the node's output JSON, e.g. "From" or "body.amount"


@dataclass(frozen=True)
class N8nNode:
    """How the step is written in an n8n workflow (checked against the n8n version in N8N_VERSION).

    `build_params` turns the step's settings into the node's `parameters`. References in the settings are
    already n8n expressions when it is called (the exporter rewrites them)."""

    type: str
    type_version: float
    build_params: Callable[[Settings], dict[str, Any]]
    # For steps whose output is whatever data they receive (a webhook body): where any field sits in n8n's
    # output, e.g. "body.{field}". None: n8n has no such data, so references to it cannot be mapped.
    dynamic_path: str | None = None


@dataclass(frozen=True)
class CatalogueEntry:
    key: str  # "<app>.<operation>", e.g. "slack.send_message"
    app: str  # as shown to people: "Slack"
    operation: str  # as shown to people: "Send message"
    kind: Kind
    description: str
    settings: tuple[Setting, ...] = ()
    output_fields: tuple[OutputField, ...] = ()
    # True when the step's output is the data it receives (webhook body, manual input, appended row), so any
    # field name may be referenced; `output_fields` then lists only the fields that are always there.
    dynamic_output: bool = False
    executable: bool = False  # the built-in runner can execute it (app/runtime/executor.py)
    n8n: N8nNode | None = None
    aliases: tuple[str, ...] = field(default=())  # other names the planner may use for this operation

    @property
    def step_kind(self) -> str:
        """The workflow step kind: a logic entry is a "condition" step."""
        return "condition" if self.kind == "logic" else self.kind

    def setting(self, name: str) -> Setting | None:
        """The setting called `name` or one of its aliases."""
        wanted = name.strip().lower()
        return next((s for s in self.settings if s.name == wanted), None) or next(
            (s for s in self.settings if wanted in s.aliases), None)

    @property
    def required_settings(self) -> tuple[Setting, ...]:
        return tuple(s for s in self.settings if s.required)

    def produces(self, name: str) -> bool:
        """Whether a later step may reference {{step.<name>}}. Nested paths count by their first part."""
        first = name.split(".", 1)[0]
        return self.dynamic_output or any(f.name == first for f in self.output_fields)

    def n8n_path(self, name: str) -> str | None:
        """Where field `name` of this step is in n8n's output; None when it cannot be mapped (never guessed)."""
        first, _, rest = name.partition(".")
        known = next((f for f in self.output_fields if f.name == first), None)
        if known is not None:
            return f"{known.n8n_path}.{rest}" if rest else known.n8n_path
        if self.dynamic_output and self.n8n and self.n8n.dynamic_path:
            return self.n8n.dynamic_path.format(field=name)
        return None
