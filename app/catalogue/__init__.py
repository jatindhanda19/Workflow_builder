"""The app catalogue: the single source of truth for which apps and operations Flowwright knows.

    from app.catalogue import find, get
    get("slack.send_message")                    # by key
    find("slack", "Post message", "action")      # by the names a planner or user uses

Apps outside the catalogue are not rejected here; callers decide (they are "unverified" steps).
"""

import re

from app.catalogue.entries import ENTRIES
from app.catalogue.models import CatalogueEntry, N8nNode, OutputField, Setting
from app.catalogue.n8n import N8N_VERSION

CATALOGUE: dict[str, CatalogueEntry] = {entry.key: entry for entry in ENTRIES}

# Other names people give the catalogue's apps.
APP_ALIASES = {
    "google sheet": "Google Sheets", "sheets": "Google Sheets", "gsheets": "Google Sheets",
    "google mail": "Gmail", "http": "HTTP Request", "api": "HTTP Request", "condition": "If",
    "cron": "Schedule", "manual trigger": "Manual",
}

__all__ = ["APP_ALIASES", "CATALOGUE", "N8N_VERSION", "CatalogueEntry", "N8nNode", "OutputField", "Setting",
           "app_name", "default_apps", "entries_for_app", "find", "get", "prompt_section"]


def get(key: str) -> CatalogueEntry | None:
    return CATALOGUE.get(key)


def app_name(app: str | None) -> str | None:
    """The catalogue's name for an app ("google sheet" → "Google Sheets"); None if the app is not catalogued."""
    wanted = _plain(app or "")
    names = {_plain(entry.app): entry.app for entry in ENTRIES}
    return names.get(wanted) or APP_ALIASES.get(wanted)


def entries_for_app(app: str | None, kind: str | None = None) -> list[CatalogueEntry]:
    """The app's operations, optionally only those of one step kind (trigger, action or condition)."""
    name = app_name(app)
    return [e for e in ENTRIES if e.app == name and (kind is None or e.step_kind == kind or e.kind == kind)]


def find(app: str | None, operation: str | None, kind: str | None = None) -> CatalogueEntry | None:
    """The entry for an app's operation, matched by its name or an alias, ignoring case and punctuation.

    An operation the catalogue does not list returns None: it is never mapped to the app's nearest operation."""
    wanted = _plain(operation or "")
    for entry in entries_for_app(app, kind):
        if wanted in {_plain(entry.operation), *(_plain(a) for a in entry.aliases)}:
            return entry
    return None


def default_apps(kind: str) -> list[str]:
    """Catalogue apps that can be a step of this kind, offered when the planner suggests none."""
    return list(dict.fromkeys(e.app for e in ENTRIES if e.step_kind == kind))


def prompt_section() -> str:
    """The catalogue as the planner sees it: one line per operation, with its setting names and kinds.

        - Slack / Send message (action): channel* (channel), message* (long_text)
    """
    lines = []
    for entry in ENTRIES:
        settings = ", ".join(
            f"{s.name}{'*' if s.required else ''} ({s.type}{': ' + ' | '.join(s.choices) if s.type == 'choice' else ''})"
            for s in entry.settings) or "no settings"
        if entry.kind == "logic":  # conditions follow rule 5 of the prompt (value kind number or text)
            settings = ("field* (field), operator* (operator), value (number or text, as in rule 5); more "
                        "comparisons: field_2, operator_2, value_2 (up to 3) and combinator (choice: and | or)")
        lines.append(f"- {entry.app} / {entry.operation} ({entry.step_kind}): {settings}")
    return "\n".join(lines)


def _plain(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))
