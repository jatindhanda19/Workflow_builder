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


def test_sessions_are_scoped_to_their_owner(tmp_path):
    store = SessionStore(tmp_path / "sessions.db")
    assert store.save("abc123", _generated(), owner_id="alice")
    assert store.get("abc123", owner_id="bob") is None and store.get("abc123") is None
    assert not store.save("abc123", _generated(), owner_id="bob")  # bob cannot take alice's id
    assert not store.delete("abc123", owner_id="bob")
    assert store.exists("abc123") and store.get("abc123", owner_id="alice") is not None


def test_save_with_an_old_version_is_refused(tmp_path):
    store = SessionStore(tmp_path / "sessions.db")
    assert store.save("abc123", _generated(), owner_id="alice", expected_version=0)  # new: version 1
    assert not store.save("abc123", _generated(), owner_id="alice", expected_version=0)  # it exists now
    version = store.load("abc123", owner_id="alice").version
    assert store.save("abc123", _generated(), owner_id="alice", expected_version=version)
    assert not store.save("abc123", _generated(), owner_id="alice", expected_version=version)  # changed since


def test_a_database_from_before_owners_is_upgraded(tmp_path):
    path = tmp_path / "sessions.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, state TEXT NOT NULL, updated_at TEXT NOT NULL)")
        db.execute("INSERT INTO sessions VALUES ('old', ?, '2026-01-01T00:00:00+00:00')",
                   (_generated().model_dump_json(),))
    store = SessionStore(path)
    assert store.get("old") is not None  # kept, with no owner: no signed-in user can reach it
    assert store.get("old", owner_id="alice") is None
    assert store.save("new", _generated(), owner_id="alice")
