from dataclasses import dataclass
from enum import IntEnum
from typing import Literal

Category = Literal["setup", "trigger", "logic", "data", "action"]
FieldKind = Literal[
    "text", "free_text", "name", "field_list", "mapping", "choice", "email_list",
    "time", "timezone", "slack_channel", "day_of_month", "duration",
]


class Phase(IntEnum):
    """Question order: goal → trigger → location → event → condition → sources → channel → content → …"""

    GOAL = 0
    TRIGGER = 1
    LOCATION = 2
    EVENT = 3
    CONDITION = 4
    SOURCE = 5
    RECIPIENT_SOURCE = 6
    CHANNEL = 7
    RECIPIENTS = 8
    CONTENT = 9
    SUBJECT = 10
    DELIVERY = 11
    DUPLICATES = 12
    OPTIONAL = 13


@dataclass(frozen=True)
class Choice:
    value: str
    label: str
    synonyms: tuple[str, ...] = ()
    echo: bool = False
    implies: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class When:
    """Condition on a field value, an intent attribute (intent.*) or a planner fact (fact.*)."""

    key: str
    values: tuple[str, ...]


@dataclass(frozen=True)
class FieldDef:
    name: str
    label: str
    description: str
    question: str
    phase: Phase
    kind: FieldKind = "text"
    required: bool = True
    choices: tuple[Choice, ...] = ()
    applies_when: tuple[When, ...] = ()
    required_when: tuple[When, ...] = ()
    depends_on: tuple[str, ...] = ()
    vague: frozenset[str] = frozenset()
    nouns: tuple[str, ...] = ()
    node: str = ""

    @property
    def key(self) -> str:
        return f"{self.node}.{self.name}"

    def choice(self, value: object) -> Choice | None:
        return next((c for c in self.choices if c.value == value), None)


@dataclass(frozen=True)
class NodeType:
    """One node type. Setup nodes hold planning questions and are never emitted.

    selector:      (selector field key, choice) that picks this node, e.g. ("action.channel", Choice("email", …))
    selected_when: extra conditions for the node to be part of the plan
    emit_when:     conditions for the node to appear in the generated workflow (defaults to selected)
    workflow_type: JSON "type"; {field} placeholders are filled from the node's values
    display_name:  JSON "name" template
    summary:       one-line key parameter for the diagram; first template whose placeholders are all known wins
    per_item:      runs once per item when a Loop node is present
    """

    id: str
    label: str
    category: Category
    stage: int
    fields: tuple[FieldDef, ...] = ()
    selector: tuple[str, Choice] | None = None
    selected_when: tuple[When, ...] = ()
    emit_when: tuple[When, ...] = ()
    workflow_type: str | None = None
    display_name: str = ""
    summary: tuple[str, ...] = ()
    per_item: bool = False

    @property
    def conditions(self) -> tuple[When, ...]:
        if self.selector is None:
            return self.selected_when
        key, choice = self.selector
        return (When(key, (choice.value,)), *self.selected_when)
