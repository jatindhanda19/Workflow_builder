"""Runs a generated workflow for real, for four step types only. Any other step makes the whole run refused
before anything is executed, so a workflow is never half run.

    Manual / Webhook trigger  its data is the input JSON given to the run (a stand-in for the webhook body)
    If                        compares a field of earlier data with the value; follows the true or false edge
    HTTP Request              calls the URL (method, optional body) and returns the status and response
    Slack message             posts the message to the Slack incoming webhook in SLACK_WEBHOOK_URL

Secrets come only from environment variables. Workflow parameters come from the chat, so they never hold one,
and a secret is never put in a step's output or error.
"""

import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Mapping

import requests

from app.pipeline.generation.schema import Edge, Workflow, WorkflowNode
from app.pipeline.normalize import ORDERING_OPERATORS, UNARY_OPERATORS

SLACK_WEBHOOK_ENV = "SLACK_WEBHOOK_URL"
TIMEOUT_SECONDS = 10
MAX_BODY_CHARS = 2000
TRIGGER_APPS = frozenset({"manual", "webhook"})
SLACK_OPERATION = re.compile(r"message|send|post|notif", re.IGNORECASE)
EXPRESSION = re.compile(r"\{\{\s*([\w.$-]+)\s*\}\}")
URL_KEYS, BODY_KEYS, TEXT_KEYS = ("url", "endpoint"), ("body", "payload", "data", "json"), ("message", "text", "content")
MISSING = object()

Send = Callable[..., Any]  # requests.request(method, url, **kwargs)


class RunError(ValueError):
    """The workflow cannot run here; nothing was executed."""


class StepError(RuntimeError):
    """One step failed; the steps after it are not run."""


@dataclass
class StepResult:
    id: str
    name: str
    status: str  # ok | failed | skipped
    output: Any = None
    error: str | None = None


def unsupported(workflow: Workflow) -> list[str]:
    """The steps this runner cannot execute, e.g. ["Gmail: Send email"]."""
    return [node.name for node in workflow.nodes if _kind(node) is None]


def run_workflow(workflow: Workflow, data: dict[str, Any] | None = None, send: Send = requests.request,
                 env: Mapping[str, str] = os.environ) -> list[StepResult]:
    """Runs every reachable step in order. A step runs when an edge into it is active: its source ran, and
    for a condition, the edge's branch is the condition's result. Other steps are reported as skipped."""
    missing = unsupported(workflow)
    if missing:
        raise RunError("This runner supports Manual or Webhook triggers, If, HTTP Request and Slack messages "
                       f"only. Not supported: {', '.join(missing)}.")
    incoming: dict[str, list[Edge]] = defaultdict(list)
    for edge in workflow.edges:
        incoming[edge.target].append(edge)
    outputs: dict[str, Any] = {}  # data of every step that ran, by step id
    results: list[StepResult] = []
    trigger = workflow.nodes[0]
    for node in workflow.nodes:
        if node.type == "end":
            continue
        if node is not trigger and not any(_active(edge, outputs) for edge in incoming[node.id]):
            results.append(StepResult(node.id, node.name, "skipped", error="not reached"))
            continue
        try:
            outputs[node.id] = _run_step(node, data or {}, outputs, trigger.id, send, env)
            results.append(StepResult(node.id, node.name, "ok", outputs[node.id]))
        except StepError as exc:
            results.append(StepResult(node.id, node.name, "failed", error=str(exc)))
    return results


def _kind(node: WorkflowNode) -> str | None:
    app = str(node.parameters.get("app") or "").lower()
    if node.type == "end":
        return "end"
    if node.type == "condition":
        return "if"
    if node.type.endswith("_trigger"):
        return "trigger" if app in TRIGGER_APPS else None
    if app == "http request":
        return "http"
    if app == "slack" and SLACK_OPERATION.search(str(node.parameters.get("operation") or "")):
        return "slack"
    return None


def _active(edge: Edge, outputs: dict[str, Any]) -> bool:
    if edge.source not in outputs:
        return False
    return edge.branch is None or str(outputs[edge.source]["result"]).lower() == edge.branch


def _run_step(node: WorkflowNode, data: dict, outputs: dict, trigger_id: str, send: Send, env: Mapping) -> Any:
    kind, params = _kind(node), node.parameters
    if kind == "trigger":
        return data
    if kind == "if":
        return _condition(params, outputs, trigger_id)
    if kind == "http":
        return _http(params, outputs, trigger_id, send)
    return _slack(params, outputs, trigger_id, send, env)


# -- steps ----------------------------------------------------------------------------------------------
def _condition(params: dict, outputs: dict, trigger_id: str) -> dict:
    field = EXPRESSION.sub(r"\1", str(params.get("field") or "")).strip()
    left, operator = _lookup(field, outputs, trigger_id), params.get("operator")
    if operator in UNARY_OPERATORS:  # a missing field is what "is empty" checks for
        empty = left is MISSING or left in (None, "", [], {})
        return {"result": empty == (operator == "is_empty"), "field": field,
                "actual": None if left is MISSING else left, "operator": operator}
    if left is MISSING:
        raise StepError(f'the data has no "{field}"')
    right = _render(params.get("value"), outputs, trigger_id)
    return {"result": _compare(left, str(operator), right), "field": field, "actual": left, "operator": operator,
            "value": right}


def _http(params: dict, outputs: dict, trigger_id: str, send: Send) -> dict:
    url = _render(_first(params, URL_KEYS), outputs, trigger_id)
    if not isinstance(url, str) or not re.match(r"https?://", url):
        raise StepError(f"the URL {url!r} is not an http(s) URL")
    body = _render(_first(params, BODY_KEYS), outputs, trigger_id)
    method = str(params.get("method") or ("POST" if body is not None else "GET")).upper()
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError:
            pass
    kwargs = {"json": body} if isinstance(body, (dict, list)) else {"data": body} if body is not None else {}
    try:
        response = send(method, url, timeout=TIMEOUT_SECONDS, **kwargs)
    except requests.RequestException as exc:
        raise StepError(f"{method} {url} failed: {exc}") from exc
    if response.status_code >= 400:
        raise StepError(f"{method} {url} returned HTTP {response.status_code}: {response.text[:200]}")
    try:
        content = response.json()
    except ValueError:
        content = response.text[:MAX_BODY_CHARS]
    return {"status": response.status_code, "body": content}


def _slack(params: dict, outputs: dict, trigger_id: str, send: Send, env: Mapping) -> dict:
    webhook = env.get(SLACK_WEBHOOK_ENV, "").strip()
    if not webhook:
        raise StepError(f"set {SLACK_WEBHOOK_ENV} to a Slack incoming-webhook URL to send Slack messages")
    text = _render(_first(params, TEXT_KEYS), outputs, trigger_id)
    if not text:
        raise StepError("the step has no message text")
    try:
        response = send("POST", webhook, json={"text": str(text)}, timeout=TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise StepError(f"posting to Slack failed ({type(exc).__name__})") from exc  # the message holds the URL
    if response.status_code >= 400:
        raise StepError(f"Slack returned HTTP {response.status_code}: {response.text[:200]}")
    # An incoming webhook always posts to the channel it was created for, whatever the step's channel says.
    return {"status": response.status_code, "posted": str(text), "channel": "the webhook's channel"}


# -- data -----------------------------------------------------------------------------------------------
def _lookup(path: str, outputs: dict, trigger_id: str) -> Any:
    """A step's data by reference: "check_amount.result", or a field of the trigger data, where the first part
    may name the entity ("invoice.amount" is data["invoice"]["amount"] or data["amount"])."""
    parts = [p for p in path.split(".") if p]
    if not parts:
        return MISSING
    if parts[0] in outputs:
        return _walk(outputs[parts[0]], parts[1:])
    found = _walk(outputs.get(trigger_id), parts)
    return found if found is not MISSING or len(parts) == 1 else _walk(outputs.get(trigger_id), parts[1:])


def _walk(value: Any, parts: list[str]) -> Any:
    for part in parts:
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return MISSING
    return value


def _render(value: Any, outputs: dict, trigger_id: str) -> Any:
    """Fills {{step.field}} references with data of earlier steps. A whole-value reference keeps its type."""
    if not isinstance(value, str):
        return value
    whole = EXPRESSION.fullmatch(value.strip())
    if whole:
        return _resolved(whole.group(1), outputs, trigger_id)

    def text(match: re.Match) -> str:
        found = _resolved(match.group(1), outputs, trigger_id)
        return found if isinstance(found, str) else json.dumps(found, ensure_ascii=False)

    return EXPRESSION.sub(text, value)


def _resolved(path: str, outputs: dict, trigger_id: str) -> Any:
    found = _lookup(path, outputs, trigger_id)
    if found is MISSING:
        raise StepError(f'{{{{{path}}}}} has no data: no earlier step or input field "{path}"')
    return found


def _compare(left: Any, operator: str, right: Any) -> bool:
    a, b = _number(left), _number(right)
    if operator in ORDERING_OPERATORS:
        if a is None or b is None:
            raise StepError(f"cannot compare {left!r} and {right!r} as numbers")
        return {"greater_than": a > b, "greater_than_or_equal": a >= b, "less_than": a < b,
                "less_than_or_equal": a <= b}[operator]
    same = a == b if a is not None and b is not None else str(left).strip().lower() == str(right).strip().lower()
    if operator in ("equals", "not_equals"):
        return same if operator == "equals" else not same
    if operator in ("contains", "not_contains"):
        inside = (str(right).lower() in (str(i).lower() for i in left) if isinstance(left, list)
                  else str(right).lower() in str(left).lower())
        return inside if operator == "contains" else not inside
    raise StepError(f'unknown comparison "{operator}"')


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[₹$€£,\s]", "", str(value))
    try:
        return float(cleaned)
    except ValueError:
        return None


def _first(params: dict, keys: tuple[str, ...]) -> Any:
    return next((params[k] for k in keys if params.get(k) not in (None, "")), None)
