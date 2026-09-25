from string import Formatter
from typing import Any

from app.registry.model import FieldDef, NodeType

FieldValue = str | list[str]
NO_ECHO_KINDS = ("free_text", "mapping")


def display_value(fdef: FieldDef, value: FieldValue | None) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(value)
    if fdef.choices:
        choice = fdef.choice(value)
        return choice.label if choice else value
    if fdef.kind == "time":
        return twelve_hour(value)
    if fdef.kind == "day_of_month":
        return "the last day" if value == "last" else f"day {value}"
    return value


def echo_text(fdef: FieldDef, value: FieldValue) -> str | None:
    """The short echo for "Got it, …", or None when echoing would read oddly."""
    if fdef.kind in NO_ECHO_KINDS:
        return None
    if fdef.choices:
        choice = fdef.choice(value)
        return choice.label if choice and choice.echo else None
    return display_value(fdef, value)


def plural(word: str) -> str:
    return word if word.endswith("s") else f"{word}s"


def twelve_hour(hh_mm: str) -> str:
    hours, minutes = (int(part) for part in hh_mm.split(":"))
    suffix = "AM" if hours < 12 else "PM"
    return f"{hours % 12 or 12}:{minutes:02d} {suffix}"


OPERATOR_SYMBOLS = {"equals": "=", "not_equals": "≠", "greater_than": ">", "less_than": "<", "contains": "contains"}


def template_values(node: NodeType, parameters: dict[str, Any]) -> dict[str, str]:
    """Display strings for a node's parameters, keyed by parameter name (choices shown as labels)."""
    shown: dict[str, str] = {}
    for name, value in parameters.items():
        fdef = next((f for f in node.fields if f.name == name), None)
        if isinstance(value, dict):
            shown[name] = ", ".join(f"{k} ← {v}" for k, v in value.items())
        elif fdef is not None:
            shown[name] = display_value(fdef, value)
        else:
            shown[name] = ", ".join(map(str, value)) if isinstance(value, list) else str(value)
    if "operator" in parameters:
        shown["operator_symbol"] = OPERATOR_SYMBOLS.get(str(parameters["operator"]), str(parameters["operator"]))
    return shown


def fill_template(template: str, values: dict[str, str]) -> str | None:
    """Format a template, or None if any placeholder is unknown."""
    names = [name for _, name, _, _ in Formatter().parse(template) if name]
    if any(name not in values or values[name] == "" for name in names):
        return None
    return template.format_map(values)


def summary_line(node: NodeType, parameters: dict[str, Any], extra: dict[str, str] | None = None) -> str:
    """The node's one-line key parameter, e.g. "Label: Finance" or "Amount > ₹10,000"."""
    values = {**template_values(node, parameters), **(extra or {})}
    return next((line for t in node.summary if (line := fill_template(t, values))), "")
