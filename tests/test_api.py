"""The HTTP API a chat UI is driven by: structured steps, the reply in parts, retry options, a new-workflow
endpoint and CORS. The LLM is scripted, so no API key is needed."""

import pytest
from fastapi.testclient import TestClient
from test_clarification import ScriptedLLM

import app.main as main
from app.pipeline.graph import build_graph
from app.pipeline.planner_schema import NextQuestion, PlannedParam, PlannedStep, TurnPlan
from app.state.store import SessionStore

REQUEST = ("Every weekday, check the Orders spreadsheet in Google Sheets and email me with Gmail if there are "
           "new rows")
NAME = "Weekday orders email"


def _p(name, kind="text", value=None, evidence=None, label=None):
    return PlannedParam(name=name, label=label or name.capitalize(), kind=kind, value=value,
                        evidence=evidence or value)


def _plan(question=None, options=(), **values):
    """The whole workflow as the planner returns it each turn; `values` are this turn's new answers."""
    def v(name):
        return values.get(name)

    steps = [
        PlannedStep(id="schedule_trigger", kind="trigger", app="Schedule", app_evidence="Every weekday",
                    operation="Every weekday", params=[_p("time", "time", v("time")),
                                                       _p("timezone", "timezone", v("timezone"))]),
        PlannedStep(id="get_new_rows", kind="action", app="Google Sheets", app_evidence="Google Sheets",
                    operation="Get new rows", params=[_p("spreadsheet", value=v("spreadsheet"),
                                                         evidence="Orders spreadsheet" if v("spreadsheet") else None)]),
        # A condition needs a field and a comparison to be complete; both come from "if there are new rows".
        PlannedStep(id="check_new_rows", kind="condition", app="If", operation="New rows found", params=[
            _p("field", "field", v("field"), "new rows", label="Rows field"),
            _p("operator", "operator", v("operator"), "if there are new rows", label="Comparison")]),
        PlannedStep(id="send_email", kind="action", app="Gmail", app_evidence="Gmail", operation="Send email",
                    params=[_p("to", "email", v("to")), _p("subject", value=v("subject"))]),
    ]
    target, text = question or (None, None)
    next_question = NextQuestion(target=target, question=text, options=list(options)) if question else None
    return TurnPlan(message_kind="build", workflow_name=NAME, steps=steps, next_question=next_question)


TIME_Q = ("schedule_trigger.time", "What time should it run?")
CONVERSATION = [
    (REQUEST, _plan(TIME_Q, ["9:00 AM", "6:00 PM"], spreadsheet="Orders", field="{{get_new_rows.rows}}",
                    operator="is_not_empty")),
    ("evening", _plan(TIME_Q, ["9:00 AM", "6:00 PM"], time="evening")),
    ("6:00 PM", _plan(("schedule_trigger.timezone", "Which timezone is that in?"), ["Asia/Kolkata", "UTC"],
                      time="6:00 PM")),
    ("Asia/Kolkata", _plan(("send_email.to", "Who should get the email?"), timezone="Asia/Kolkata")),
    ("you@example.com", _plan(("send_email.subject", "What should the subject be?"), ["New orders today"],
                              to="you@example.com")),
    ("New orders today", _plan(subject="New orders today")),
]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", SessionStore(tmp_path / "sessions.db"))
    graph = build_graph(ScriptedLLM(*(plan for _, plan in CONVERSATION)))

    def scripted_graph():  # no parameters: FastAPI would read a defaulted one as a query parameter
        return graph

    main.app.dependency_overrides[main.get_graph] = scripted_graph
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def _chat(client, session_id, message):
    response = client.post("/chat", json={"session_id": session_id, "message": message})
    assert response.status_code == 200, response.text
    return response.json()


def _param(body, key):
    return next(p for s in body["steps"] for p in s["params"] if p["key"] == key)


def test_conversation_drives_the_ui(client):
    turns, session = [], None
    for message, _ in CONVERSATION:
        turns.append(_chat(client, session, message))
        session = turns[0]["session_id"]
    first, second, third, last = turns[0], turns[1], turns[2], turns[-1]

    assert first["name"] == NAME
    assert [s["kind"] for s in first["steps"]] == ["trigger", "action", "condition", "action"]
    assert first["asking_field"] == "schedule_trigger.time"
    # The condition's field and comparison are stored in turn 1 too, after the spreadsheet.
    assert first["parts"]["saved"].startswith("Spreadsheet: Orders")
    assert [r["role"] for r in first["parts"]["plan"]] == ["When", "Then", "If", "Then"]
    assert first["parts"]["plan"][2]["label"] == "New rows found"
    assert first["parts"]["lead"] == "Here's the plan:"
    assert first["parts"]["context"].startswith("Step 1 of 4")

    question = second["question"]
    assert "not an exact time" in question["note"] and question["text"] == "What time should it run?"
    assert question["options"][:3] == ["5:00 PM", "6:00 PM", "7:00 PM"]
    assert _param(second, "schedule_trigger.time")["status"] == "ambiguous"
    assert question["note"].rstrip(".") in second["reply"] and question["text"] in second["reply"]
    assert second["parts"]["note"] == question["note"] and second["parts"]["text"] == question["text"]

    assert third["parts"]["saved"] == "Time: 6:00 PM"
    time = _param(third, "schedule_trigger.time")
    assert (time["value"], time["display"]) == ("18:00", "6:00 PM")
    assert next(f for f in third["fields"] if f["key"] == "schedule_trigger.time")["display"] == "6:00 PM"

    assert last["mode"] == "ready" and last["workflow"] is not None
    assert last["parts"]["text"] == f'Your workflow "{NAME}" is ready.'
    assert all(p["status"] == "filled" for s in last["steps"] for p in s["params"])

    history = client.get(f"/sessions/{session}").json()
    assert all(m["parts"] for m in history["messages"] if m["role"] == "assistant")

    fresh = client.post(f"/sessions/{session}/new")
    assert fresh.status_code == 200
    body = fresh.json()
    assert body["steps"] == [] and body["workflow"] is None and body["messages"] == []
    assert body["mode"] == "collecting" and body["name"] is None
    assert [p["reason"] for p in body["previous_workflows"]] == ["new_workflow"]
    assert client.post("/sessions/unknown/new").status_code == 404


def test_cors_preflight_allows_the_ui_origin(client):
    response = client.options("/chat", headers={"Origin": "http://localhost:5173",
                                                "Access-Control-Request-Method": "POST"})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
