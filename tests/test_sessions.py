"""Sessions are stored on every turn, listed per user and continued after a restart. The LLM is scripted."""

import sqlite3

import pytest
from auth_test_utils import authenticate_test_client
from fastapi.testclient import TestClient
from test_api import CONVERSATION
from test_clarification import ScriptedLLM

import app.main as main
from app.pipeline.graph import build_graph
from app.state.models import Message, WorkflowState
from app.state.store import SessionStore, session_title


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "sessions.db"
    monkeypatch.setattr(main, "store", SessionStore(path))
    graph = build_graph(ScriptedLLM(*(plan for _, plan in CONVERSATION)))
    main.app.dependency_overrides[main.get_graph] = lambda: graph
    yield path
    main.app.dependency_overrides.clear()


def _chat(client, session_id, message):
    response = client.post("/chat", json={"session_id": session_id, "message": message})
    assert response.status_code == 200, response.text
    return response.json()


def test_session_survives_a_restart_and_the_chat_continues(db_path, monkeypatch):
    client = TestClient(main.app)
    authenticate_test_client(client)
    session = None
    for message, _ in CONVERSATION[:3]:
        session = _chat(client, session, message)["session_id"]  # the first turn gets the server's id

    monkeypatch.setattr(main, "store", SessionStore(db_path))  # a restarted server: nothing in memory
    listed = client.get("/sessions").json()
    assert [s["id"] for s in listed] == [session]
    opened = client.get(f"/sessions/{session}").json()
    assert [m["role"] for m in opened["messages"]] == ["user", "assistant"] * 3
    assert opened["messages"][0]["content"] == CONVERSATION[0][0]

    fourth = _chat(client, session, CONVERSATION[3][0])
    assert fourth["session_id"] == session
    time = next(p for s in fourth["steps"] for p in s["params"] if p["key"] == "schedule_trigger.time")
    assert time["value"] == "18:00"  # answered before the restart, still there
    assert len(client.get(f"/sessions/{session}").json()["messages"]) == 8


def test_every_turn_is_saved(db_path):
    client = TestClient(main.app)
    authenticate_test_client(client)
    session = _chat(client, None, CONVERSATION[0][0])["session_id"]
    assert len(main.store.get(session, owner_id=client.get("/auth/me").json()["id"]).messages) == 2
    _chat(client, session, CONVERSATION[1][0])
    assert len(client.get(f"/sessions/{session}").json()["messages"]) == 4


def test_list_is_newest_first_with_titles(db_path):
    client = TestClient(main.app)
    owner = authenticate_test_client(client)["id"]
    main.store.save("older", WorkflowState(messages=[Message(role="user", content="Back up my Drive files")]),
                    owner_id=owner)
    main.store.save("newer", WorkflowState(name="Weekday orders email"), owner_id=owner)

    listed = client.get("/sessions").json()
    assert [(s["id"], s["title"]) for s in listed] == [("newer", "Weekday orders email"),
                                                      ("older", "Back up my Drive files")]
    assert listed[0] == {"id": "newer", "title": "Weekday orders email", "mode": "collecting",
                         "generated": False, "updated_at": listed[0]["updated_at"]}

    main.store.save("older", WorkflowState(name="Drive backup"), owner_id=owner)  # changed last: listed first
    assert [s["id"] for s in client.get("/sessions").json()] == ["older", "newer"]


def test_users_only_see_their_own_sessions(db_path):
    alice, bob = TestClient(main.app), TestClient(main.app)
    alice_id = authenticate_test_client(alice)["id"]
    authenticate_test_client(bob)
    main.store.save("alices", WorkflowState(name="Private"), owner_id=alice_id)

    assert [s["id"] for s in alice.get("/sessions").json()] == ["alices"]
    assert bob.get("/sessions").json() == []
    assert bob.get("/sessions/alices").status_code == 404
    assert bob.delete("/sessions/alices").status_code == 404
    assert TestClient(main.app).get("/sessions").status_code == 401


def test_title_keeps_its_value_when_new_workflow_clears_the_chat(db_path):
    client = TestClient(main.app)
    owner = authenticate_test_client(client)["id"]
    main.store.save("s1", WorkflowState(name="Weekday orders email"), owner_id=owner)
    assert client.post("/sessions/s1/new").status_code == 200
    assert client.get("/sessions").json()[0]["title"] == "Weekday orders email"


def test_title_is_the_workflow_name_or_the_first_message():
    assert session_title(WorkflowState()) is None
    assert session_title(WorkflowState(name="  Invoice alerts ")) == "Invoice alerts"
    long = "When an invoice arrives in Gmail and the amount is above one lakh, notify the finance team"
    title = session_title(WorkflowState(messages=[Message(role="user", content=long)]))
    assert len(title) == 60 and title.endswith("…")


def test_sessions_saved_before_the_list_existed_get_titles(tmp_path):
    path = tmp_path / "sessions.db"
    state = WorkflowState(messages=[Message(role="user", content="Post new GitHub issues to Slack")])
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, state TEXT NOT NULL, updated_at TEXT NOT NULL, "
                   "owner_id TEXT, version INTEGER NOT NULL DEFAULT 0)")
        db.execute("INSERT INTO sessions VALUES ('old', ?, '2026-10-01T00:00:00+00:00', 'alice', 3)",
                   (state.model_dump_json(),))
    [summary] = SessionStore(path).list_sessions("alice")
    assert (summary.id, summary.title, summary.mode, summary.generated) == (
        "old", "Post new GitHub issues to Slack", "collecting", False)
