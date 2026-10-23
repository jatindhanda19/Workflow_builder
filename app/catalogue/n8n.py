"""Small builders for n8n node parameters, shared by the catalogue entries.

Every shape here was checked against the n8n source at N8N_VERSION (packages/nodes-base and packages/workflow).
"""

import re
import uuid
from typing import Any

N8N_VERSION = "2.44.6"  # the n8n release the node types, versions and parameters below were checked against

# Our comparison → n8n's filter operator (packages/workflow/src/node-parameters/filter-parameter.ts).
# Ordering comparisons are numeric; equals / not_equals are numeric only when the value is a number.
_NUMBER_OPERATIONS = {"greater_than": "gt", "greater_than_or_equal": "gte", "less_than": "lt",
                      "less_than_or_equal": "lte", "equals": "equals", "not_equals": "notEquals"}
_STRING_OPERATIONS = {"equals": "equals", "not_equals": "notEquals", "contains": "contains",
                      "not_contains": "notContains", "is_empty": "empty", "is_not_empty": "notEmpty"}
_UNARY = frozenset({"is_empty", "is_not_empty"})
_ORDERING = frozenset({"greater_than", "greater_than_or_equal", "less_than", "less_than_or_equal"})

# A schedule's days → the day-of-week field of a cron expression (0 = Sunday).
_CRON_DAYS = {"every day": "*", "weekdays": "1-5", "weekends": "0,6", "sunday": "0", "monday": "1",
              "tuesday": "2", "wednesday": "3", "thursday": "4", "friday": "5", "saturday": "6"}
SCHEDULE_DAYS = tuple(_CRON_DAYS)


def resource_locator(value: str, mode: str) -> dict[str, Any]:
    """n8n's resource-locator value, e.g. a Slack channel by name: {"__rl": True, "mode": "name", "value": "#x"}."""
    return {"__rl": True, "mode": mode, "value": value}


def cron_expression(time: str, days: str | None) -> str:
    """"18:00" on weekdays → "0 18 * * 1-5" (minute hour day-of-month month day-of-week)."""
    hour, minute = (int(part) for part in time.split(":")[:2])
    return f"{minute} {hour} * * {_CRON_DAYS[(days or 'every day').lower()]}"


def if_condition(left: str, operator: str, right: Any) -> dict[str, Any]:
    """One condition of an If node (filter version 4, used by If v2.4).

    `left` is already an n8n expression. The id is derived from the condition, so the same workflow always
    exports the same JSON."""
    number = _as_number(right)
    if operator in _ORDERING or (operator in ("equals", "not_equals") and number is not None):
        kind, operation, right_value = "number", _NUMBER_OPERATIONS[operator], number
    else:
        kind, operation, right_value = "string", _STRING_OPERATIONS[operator], "" if right is None else str(right)
    op: dict[str, Any] = {"type": kind, "operation": operation}
    if operator in _UNARY:
        op["singleValue"] = True  # "is empty" compares with nothing
        right_value = ""
    condition_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"flowwright:{left}|{operator}|{right_value}"))
    return {"id": condition_id, "leftValue": left, "rightValue": right_value, "operator": op}


def if_conditions(conditions: list[dict[str, Any]], combinator: str = "and") -> dict[str, Any]:
    """The If node's `conditions` parameter: the conditions joined with "and" or "or"."""
    return {
        "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 4},
        "conditions": conditions,
        "combinator": combinator,
    }


def _as_number(value: Any) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    cleaned = re.sub(r"[₹$€£,\s]", "", str(value))
    try:
        number = float(cleaned)
    except ValueError:
        return None
    return int(number) if number.is_integer() else number
