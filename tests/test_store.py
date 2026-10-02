"""Sessions are kept in SQLite, so the workflow survives a backend restart."""

import sqlite3

from test_clarification import _generated

from app.state.store import SessionStore


def test_session_survives_a_restart(tmp_path):
    state = _generated()
    SessionStore(tmp_path / "sessions.db").save("abc123", state)
    restarted = SessionStore(tmp_path / "sessions.db")  # a new process: nothing in memory
    loaded = restarted.get("abc123")
    assert loaded is not None and loaded.workflow == state.workflow and loaded.messages == state.messages


def test_save_replaces_the_session(tmp_path):
    store = SessionStore(tmp_path / "sessions.db")
    store.save("abc123", _generated())
    store.save("abc123", _generated().model_copy(update={"name": "Renamed"}))
    assert store.get("abc123").name == "Renamed"


def test_delete_removes_the_session(tmp_path):
    store = SessionStore(tmp_path / "sessions.db")
    store.save("abc123", _generated())
    assert store.delete("abc123") and SessionStore(tmp_path / "sessions.db").get("abc123") is None
    assert not store.delete("abc123")


def test_any_session_id_is_only_a_key(tmp_path):
    store = SessionStore(tmp_path / "sessions.db")
    store.save("../evil'; DROP TABLE sessions; --", _generated())
    assert store.get("../evil'; DROP TABLE sessions; --") is not None
    assert [p.name for p in tmp_path.iterdir()] == ["sessions.db"]


def test_unreadable_state_starts_fresh(tmp_path):
    store = SessionStore(tmp_path / "sessions.db")
    store.save("abc123", _generated())
    with sqlite3.connect(tmp_path / "sessions.db") as db:
        db.execute("UPDATE sessions SET state = '{\"steps\": 5}'")
    assert store.get("abc123") is None
