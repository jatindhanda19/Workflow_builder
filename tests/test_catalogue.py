"""The app catalogue: every entry is consistent, and its n8n parameters are exactly what n8n 2.44.6 expects."""

import re

import pytest

from app.catalogue import CATALOGUE, N8N_VERSION, app_name, entries_for_app, find, get
from app.catalogue.n8n import cron_expression, if_condition
from app.pipeline.planner_schema import ParamKind
from app.pipeline.validation import _VALIDATORS as VALIDATORS

PARAM_KINDS = set(ParamKind.__args__)


# -- every entry is consistent ------------------------------------------------------------------------------
@pytest.mark.parametrize("entry", CATALOGUE.values(), ids=list(CATALOGUE))
def test_entry_is_consistent(entry):
    assert re.fullmatch(r"[a-z0-9_]+\.[a-z0-9_]+", entry.key)
    assert entry.kind in ("trigger", "action", "logic") and entry.app and entry.operation and entry.description
    names = [s.name for s in entry.settings]
    assert len(names) == len(set(names)), "setting names are unique"
    for setting in entry.settings:
        assert setting.type in PARAM_KINDS and setting.type in VALIDATORS, "each type has a validator"
        assert setting.label and setting.description
        assert bool(setting.choices) == (setting.type == "choice") or setting.type == "operator"
    fields = [f.name for f in entry.output_fields]
    assert len(fields) == len(set(fields)), "output field names are unique"
    assert entry.n8n is not None and entry.n8n.type.startswith("n8n-nodes-base.")
    if entry.dynamic_output is False:
        assert entry.n8n.dynamic_path is None


def test_aliases_do_not_collide_within_an_app():
    for app in {e.app for e in CATALOGUE.values()}:
        for kind in ("trigger", "action", "condition"):
            names = [n.lower() for e in entries_for_app(app, kind) for n in (e.operation, *e.aliases)]
            assert len(names) == len(set(names)), f"{app} {kind}"


def test_only_the_four_runnable_step_types_are_executable():
    assert {k for k, e in CATALOGUE.items() if e.executable} == {
        "manual.trigger", "webhook.trigger", "if.condition", "http_request.request", "slack.send_message"}


def test_the_n8n_nodes_are_pinned_to_the_checked_versions():
    assert N8N_VERSION == "2.44.6"
    assert {k: (e.n8n.type, e.n8n.type_version) for k, e in CATALOGUE.items()} == {
        "manual.trigger": ("n8n-nodes-base.manualTrigger", 1),
        "webhook.trigger": ("n8n-nodes-base.webhook", 2.2),
        "schedule.trigger": ("n8n-nodes-base.scheduleTrigger", 1.4),
        "gmail.new_email": ("n8n-nodes-base.gmailTrigger", 1.4),
        "gmail.send_email": ("n8n-nodes-base.gmail", 2.2),
        "slack.send_message": ("n8n-nodes-base.slack", 2.7),
        "google_sheets.append_row": ("n8n-nodes-base.googleSheets", 4.7),
        "http_request.request": ("n8n-nodes-base.httpRequest", 4.5),
        "if.condition": ("n8n-nodes-base.if", 2.4),
    }


# -- n8n parameters (golden) --------------------------------------------------------------------------------
def _params(key, **settings):
    return get(key).n8n.build_params(settings)


def test_trigger_parameters():
    assert _params("manual.trigger") == {}
    assert _params("webhook.trigger") == {"httpMethod": "POST", "path": "", "options": {}}
    assert _params("webhook.trigger", path="new-order", http_method="PUT") == {
        "httpMethod": "PUT", "path": "new-order", "options": {}}
    assert _params("schedule.trigger", time="18:00", days="weekdays") == {
        "rule": {"interval": [{"field": "cronExpression", "expression": "0 18 * * 1-5"}]}}
    assert _params("gmail.new_email", sender="boss@example.com", search="subject:invoice") == {
        "pollTimes": {"item": [{"mode": "everyMinute"}]}, "simple": True,
        "filters": {"sender": "boss@example.com", "q": "subject:invoice"}, "options": {}}
    assert _params("gmail.new_email")["filters"] == {}


def test_action_parameters():
    assert _params("gmail.send_email", to=["a@example.com", "b@example.com"], subject="Hi", message="Hello") == {
        "resource": "message", "operation": "send", "sendTo": "a@example.com, b@example.com", "subject": "Hi",
        "emailType": "text", "message": "Hello", "options": {}}
    assert _params("slack.send_message", channel="#alerts", message="New order") == {
        "resource": "message", "operation": "post", "select": "channel",
        "channelId": {"__rl": True, "mode": "name", "value": "#alerts"},
        "messageType": "text", "text": "New order", "otherOptions": {}}
    assert _params("slack.send_message", channel="C0122KQ70S7", message="x")["channelId"]["mode"] == "id"
    assert _params("http_request.request", url="https://example.com/a") == {
        "method": "GET", "url": "https://example.com/a", "options": {}}
    assert _params("http_request.request", url="https://example.com/a", body='{"a": 1}') == {
        "method": "POST", "url": "https://example.com/a", "options": {}, "sendBody": True, "contentType": "json",
        "specifyBody": "json", "jsonBody": '{"a": 1}'}


def test_google_sheets_append_parameters():
    params = _params("google_sheets.append_row", spreadsheet="https://docs.google.com/spreadsheets/d/abc/edit",
                     sheet="Orders", row="Name=={{ $json.name }}, Total=500")
    assert params["resource"] == "sheet" and params["operation"] == "append"
    assert params["documentId"] == {"__rl": True, "mode": "url",
                                    "value": "https://docs.google.com/spreadsheets/d/abc/edit"}
    assert params["sheetName"] == {"__rl": True, "mode": "name", "value": "Orders"}
    columns = params["columns"]
    assert columns["mappingMode"] == "defineBelow" and "matchingColumns" not in columns  # append is a plain insert
    assert columns["value"] == {"Name": "={{ $json.name }}", "Total": "500"}
    assert columns["schema"][0] == {"id": "Name", "displayName": "Name", "required": False, "defaultMatch": False,
                                    "display": True, "type": "string", "canBeUsedToMatch": True}
    assert _params("google_sheets.append_row", spreadsheet="1AbC", sheet="S", row=["A=1"])["documentId"]["mode"] == "id"


def test_if_parameters():
    params = _params("if.condition", field="={{ $json.amount }}", operator="greater_than", value="₹1,00,000")
    assert params["options"] == {}
    conditions = params["conditions"]
    assert conditions["combinator"] == "and"
    assert conditions["options"] == {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 4}
    [condition] = conditions["conditions"]
    assert condition["leftValue"] == "={{ $json.amount }}" and condition["rightValue"] == 100000
    assert condition["operator"] == {"type": "number", "operation": "gt"}
    assert re.fullmatch(r"[0-9a-f-]{36}", condition["id"])
    assert params == _params("if.condition", field="={{ $json.amount }}", operator="greater_than",
                             value="₹1,00,000"), "the same workflow always exports the same JSON"


@pytest.mark.parametrize("operator, value, expected", [
    ("greater_than_or_equal", "5", ("number", "gte", 5)),
    ("less_than", "2.5", ("number", "lt", 2.5)),
    ("less_than_or_equal", 10, ("number", "lte", 10)),
    ("equals", "42", ("number", "equals", 42)),
    ("equals", "paid", ("string", "equals", "paid")),
    ("not_equals", "paid", ("string", "notEquals", "paid")),
    ("contains", "urgent", ("string", "contains", "urgent")),
    ("not_contains", "test", ("string", "notContains", "test")),
])
def test_comparisons_map_to_n8n_operators(operator, value, expected):
    condition = if_condition("={{ $json.x }}", operator, value)
    assert (condition["operator"]["type"], condition["operator"]["operation"], condition["rightValue"]) == expected


@pytest.mark.parametrize("operator, operation", [("is_empty", "empty"), ("is_not_empty", "notEmpty")])
def test_empty_checks_compare_with_nothing(operator, operation):
    condition = if_condition("={{ $json.email }}", operator, None)
    assert condition["operator"] == {"type": "string", "operation": operation, "singleValue": True}
    assert condition["rightValue"] == ""


@pytest.mark.parametrize("time, days, cron", [
    ("09:00", None, "0 9 * * *"), ("18:30", "weekdays", "30 18 * * 1-5"), ("07:05", "Monday", "5 7 * * 1"),
    ("12:00", "weekends", "0 12 * * 0,6"),
])
def test_schedule_cron(time, days, cron):
    assert cron_expression(time, days) == cron


# -- lookups ------------------------------------------------------------------------------------------------
def test_find_matches_names_and_aliases_ignoring_case():
    assert find("slack", "Post message", "action").key == "slack.send_message"
    assert find("Gmail", "On new email").key == "gmail.new_email"
    assert find("gmail", "send an email", "action").key == "gmail.send_email"
    assert find("google sheet", "Add row").key == "google_sheets.append_row"
    assert find("If", "Check condition", "condition").key == "if.condition"
    assert find("HTTP Request", "POST", "action").key == "http_request.request"


def test_find_never_maps_an_unlisted_operation_or_app():
    assert find("Slack", "Create channel") is None  # a real Slack operation, but not catalogued
    assert find("Gmail", "Send email", "trigger") is None  # the right operation under the wrong kind
    assert find("Telegram", "Send message") is None
    assert app_name("Telegram") is None and app_name("sheets") == "Google Sheets"


def test_output_fields_map_to_n8n_paths_or_to_nothing():
    gmail = get("gmail.new_email")
    assert gmail.produces("from") and gmail.n8n_path("from") == "From"
    assert gmail.n8n_path("subject") == "Subject" and not gmail.produces("body")
    assert gmail.n8n_path("body") is None
    webhook = get("webhook.trigger")
    assert webhook.produces("invoice.amount") and webhook.n8n_path("invoice.amount") == "body.invoice.amount"
    manual = get("manual.trigger")
    assert manual.produces("amount") and manual.n8n_path("amount") is None  # n8n's manual trigger has no data
    assert get("slack.send_message").n8n_path("ts") == "ts"
    assert not get("if.condition").produces("result")


def test_required_settings_and_step_kind():
    entry = get("if.condition")
    assert entry.step_kind == "condition"
    assert [s.name for s in entry.required_settings] == ["field", "operator"]
    assert entry.setting("operator").choices[:2] == ("greater_than", "greater_than_or_equal")
    assert get("slack.send_message").step_kind == "action"
