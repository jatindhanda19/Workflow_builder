"""The executor runs Manual/Webhook, If, HTTP Request and Slack steps. HTTP is faked: no network, no keys."""

import pytest
import requests
from fastapi.testclient import TestClient

import app.main as main
from app.pipeline.generation.schema import Edge, Workflow, WorkflowMetadata, WorkflowNode
from app.runtime import RunError, run_workflow
from app.state.models import WorkflowState
from app.state.store import SessionStore

SLACK = {"SLACK_WEBHOOK_URL": "https://hooks.slack.test/secret-token"}


class FakeResponse:
    def __init__(self, status_code=200, body=None, text="ok"):
        self.status_code, self._body, self.text = status_code, body, text

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


class FakeHTTP:
    def __init__(self, *responses):
        self.calls, self.responses = [], list(responses)

    def __call__(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.responses.pop(0) if self.responses else FakeResponse()
        if isinstance(response, Exception):
            raise response
        return response


def _node(node_id, node_type, app, operation, **params):
    return WorkflowNode(id=node_id, type=node_type, name=f"{app}: {operation}",
                        parameters={"app": app, "operation": operation, **params})


def _workflow(nodes, edges):
    meta = WorkflowMetadata(name="Test", trigger_summary="Webhook", description="", created_at="2026-10-02T00:00:00+00:00")
    return Workflow(metadata=meta, nodes=nodes, edges=[Edge(source=a, target=b, branch=c) for a, b, c in edges])


def _invoice_workflow():
    """Webhook → amount > 100000? → yes: Slack / no: HTTP POST to the archive."""
    return _workflow([
        _node("webhook_trigger", "webhook_trigger", "Webhook", "On call"),
        _node("check_amount", "condition", "If", "Check", field="invoice.amount", operator="greater_than",
              value="100000"),
        _node("notify", "slack_send_message", "Slack", "Send message", channel="#finance",
              message="Invoice {{webhook_trigger.number}} is ₹{{invoice.amount}}"),
        _node("archive", "http_request_post", "HTTP Request", "POST", url="https://archive.test/invoices",
              body="{{webhook_trigger}}"),
    ], [("webhook_trigger", "check_amount", None), ("check_amount", "notify", "true"),
        ("check_amount", "archive", "false")])


def _statuses(results):
    return {r.id: r.status for r in results}


def test_true_branch_posts_to_slack_and_skips_the_other_branch():
    http = FakeHTTP()
    results = run_workflow(_invoice_workflow(), {"number": "INV-7", "amount": 150000}, send=http, env=SLACK)
    assert _statuses(results) == {"webhook_trigger": "ok", "check_amount": "ok", "notify": "ok", "archive": "skipped"}
    assert results[1].output["result"] is True
    assert http.calls == [("POST", SLACK["SLACK_WEBHOOK_URL"], {"json": {"text": "Invoice INV-7 is ₹150000"},
                                                                "timeout": 10})]


def test_false_branch_calls_the_http_request_with_the_trigger_data():
    http = FakeHTTP(FakeResponse(201, {"id": 9}))
    data = {"invoice": {"amount": "₹50,000"}, "number": "INV-8"}
    results = run_workflow(_invoice_workflow(), data, send=http, env=SLACK)
    assert _statuses(results)["notify"] == "skipped" and results[3].output == {"status": 201, "body": {"id": 9}}
    assert http.calls == [("POST", "https://archive.test/invoices", {"json": data, "timeout": 10})]


def test_unsupported_step_refuses_the_whole_run():
    workflow = _invoice_workflow()
    workflow.nodes[3] = _node("archive", "gmail_send_email", "Gmail", "Send email")
    http = FakeHTTP()
    with pytest.raises(RunError, match="Not supported: Gmail: Send email"):
        run_workflow(workflow, {"amount": 1}, send=http, env=SLACK)
    assert http.calls == []


def test_slack_needs_the_webhook_from_the_environment_and_never_shows_it():
    results = run_workflow(_invoice_workflow(), {"amount": 150000, "number": "1"}, send=FakeHTTP(), env={})
    assert results[2].status == "failed" and "SLACK_WEBHOOK_URL" in results[2].error
    http = FakeHTTP(requests.ConnectionError("cannot reach https://hooks.slack.test/secret-token"))
    results = run_workflow(_invoice_workflow(), {"amount": 150000, "number": "1"}, send=http, env=SLACK)
    assert results[2].status == "failed" and "secret-token" not in results[2].error


def test_missing_input_field_fails_the_condition_and_skips_the_rest():
    results = run_workflow(_invoice_workflow(), {"total": 5}, send=FakeHTTP(), env=SLACK)
    assert _statuses(results) == {"webhook_trigger": "ok", "check_amount": "failed", "notify": "skipped",
                                  "archive": "skipped"}
    assert 'no "invoice.amount"' in results[1].error


def test_http_error_status_fails_the_step():
    http = FakeHTTP(FakeResponse(500, text="boom"))
    results = run_workflow(_invoice_workflow(), {"amount": 10}, send=http, env=SLACK)
    assert results[3].status == "failed" and "HTTP 500" in results[3].error


def test_run_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", SessionStore(tmp_path / "sessions.db"))
    client = TestClient(main.app)
    assert client.post("/sessions/nope/run", json={}).status_code == 404
    main.store.save("s1", WorkflowState())
    assert client.post("/sessions/s1/run", json={}).status_code == 409
    workflow = _invoice_workflow()
    workflow.nodes[2] = _node("notify", "gmail_send_email", "Gmail", "Send email")
    main.store.save("s1", WorkflowState(workflow=workflow))
    response = client.post("/sessions/s1/run", json={"input": {"amount": 5}})
    assert response.status_code == 422 and "Gmail" in response.json()["detail"]


def test_is_empty_condition_is_true_when_the_field_is_missing():
    workflow = _workflow([
        _node("webhook_trigger", "webhook_trigger", "Webhook", "On call"),
        _node("check_email", "condition", "If", "Check", field="customer.email", operator="is_empty"),
        _node("alert", "slack_send_message", "Slack", "Send message", message="Order without email"),
    ], [("webhook_trigger", "check_email", None), ("check_email", "alert", "true")])
    for data, expected in (({"total": 5}, True), ({"customer": {"email": ""}}, True), ({"email": "a@b.co"}, False)):
        results = run_workflow(workflow, data, send=FakeHTTP(), env=SLACK)
        assert results[1].output["result"] is expected
        assert results[2].status == ("ok" if expected else "skipped")
