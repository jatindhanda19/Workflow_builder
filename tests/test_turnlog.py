"""Every /chat turn logs one JSON line: what the model proposed and what code kept, rejected or ignored."""

import json

from fastapi.testclient import TestClient
from test_clarification import ScriptedLLM, _run

import app.core.turnlog as turnlog
import app.main as main
from app.core.turnlog import turn_record
from app.pipeline.graph import build_graph
from app.pipeline.planner_schema import NextQuestion, PlannedParam, PlannedStep, TurnPlan
from app.state.models import WorkflowState
from app.state.store import SessionStore

REQUEST = "When a github issue is opened, post it to the Slack channel #dev every day at 6pm."


def _plan():
    return TurnPlan(message_kind="build", next_question=NextQuestion(target="issue_trigger.app", question="Which app?"),
                    steps=[
        PlannedStep(id="issue_trigger", kind="trigger", app="Git", app_evidence="git", operation="New issue"),
        PlannedStep(id="post", kind="action", app="Slack", app_evidence="Slack", operation="Send message", params=[
            PlannedParam(name="channel", label="Channel", kind="channel", value="#dev", evidence="#dev"),
            PlannedParam(name="time", label="Time", kind="time", value="evening", evidence="6pm"),
            PlannedParam(name="footer", label="Footer", value="Sent by the bot", evidence="sent by the bot")]),
    ])


def test_record_shows_proposed_kept_rejected_and_ignored():
    before = WorkflowState()
    after = _run(before, REQUEST, _plan())
    record = turn_record("s1", before, after, latency_ms=12)
    assert record["event"] == "turn" and record["session"] == "s1" and record["turn"] == 1
    assert record["message_kind"] == "build" and record["latency_ms"] == 12
    assert record["proposed"][0] == {"step": "issue_trigger", "kind": "trigger", "app": "Git", "values": {}}
    assert {"field": "post.channel", "value": "#dev"} in record["kept"]
    assert [r["field"] for r in record["rejected"]] == ["post.time"]
    assert record["rejected"][0]["reason"].startswith('"evening" is not an exact time')
    assert {e["field"] for e in record["ignored"]} == {"issue_trigger.app", "post.footer"}
    assert record["asked"] == "issue_trigger.app" and record["generated"] is False
    json.dumps(record)  # one JSON line


def test_chat_endpoint_logs_one_line_per_turn(tmp_path, monkeypatch):
    lines = []
    monkeypatch.setattr(turnlog.logger, "info", lines.append)
    monkeypatch.setattr(main, "store", SessionStore(tmp_path / "sessions.db"))
    graph = build_graph(ScriptedLLM(_plan(), TurnPlan(message_kind="other")))
    main.app.dependency_overrides[main.get_graph] = lambda: graph
    try:
        client = TestClient(main.app)
        session = client.post("/chat", json={"message": REQUEST}).json()["session_id"]
        client.post("/chat", json={"session_id": session, "message": "thanks"})
    finally:
        main.app.dependency_overrides.clear()
    records = [json.loads(line) for line in lines]
    assert [r["turn"] for r in records] == [1, 2] and all(r["session"] == session for r in records)
    assert records[1]["kept"] == [] and records[1]["message_kind"] == "other"
