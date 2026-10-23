
import logging
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from app.state.models import WorkflowState

logger = logging.getLogger(__name__)
SCHEMA = """CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT PRIMARY KEY,
    state      TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    owner_id   TEXT,
    version    INTEGER NOT NULL DEFAULT 0,
    title      TEXT,
    mode       TEXT,
    generated  INTEGER NOT NULL DEFAULT 0,
    created_at TEXT
)"""
# Columns added after the first release; older databases get them on startup.
MIGRATIONS = {
    "owner_id": "ALTER TABLE sessions ADD COLUMN owner_id TEXT",
    "version": "ALTER TABLE sessions ADD COLUMN version INTEGER NOT NULL DEFAULT 0",
    "title": "ALTER TABLE sessions ADD COLUMN title TEXT",
    "mode": "ALTER TABLE sessions ADD COLUMN mode TEXT",
    "generated": "ALTER TABLE sessions ADD COLUMN generated INTEGER NOT NULL DEFAULT 0",
    "created_at": "ALTER TABLE sessions ADD COLUMN created_at TEXT",
}
INDEX = "CREATE INDEX IF NOT EXISTS sessions_by_owner ON sessions (owner_id, updated_at)"
# `IS` rather than `=`, so sessions saved without an owner match only an owner of None. A title, once set, stays
# when a later state has none (e.g. after "new workflow" clears the chat).
SAVE = """INSERT INTO sessions (id, state, updated_at, owner_id, version, title, mode, generated, created_at)
VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET state = excluded.state, updated_at = excluded.updated_at,
                              version = sessions.version + 1, title = COALESCE(excluded.title, sessions.title),
                              mode = excluded.mode, generated = excluded.generated
WHERE sessions.owner_id IS excluded.owner_id AND (? IS NULL OR sessions.version = ?)"""
TITLE_CHARS = 60


@dataclass(frozen=True)
class StoredSession:
    state: WorkflowState | None  # None: the saved state could not be read
    version: int  # pass back to save() as expected_version


@dataclass(frozen=True)
class SessionSummary:
    """One row of a user's session list: enough to show it, without loading its state."""

    id: str
    title: str
    mode: str
    generated: bool
    updated_at: str


def session_title(state: WorkflowState) -> str | None:
    """The workflow's name once the planner gave one, else the start of the user's first message."""
    if state.name:
        return state.name.strip()[:TITLE_CHARS]
    first = next((m.content for m in state.messages if m.role == "user"), "")
    first = " ".join(first.split())
    if not first:
        return None
    return first if len(first) <= TITLE_CHARS else first[:TITLE_CHARS - 1].rstrip() + "…"


class SessionStore:
    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path or os.getenv("SESSION_DB", "sessions.db"))
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            # WAL lets readers work while one process writes, instead of failing with "database is locked".
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(SCHEMA)
            columns = {row[1] for row in db.execute("PRAGMA table_info(sessions)")}
            for column, statement in MIGRATIONS.items():
                if column not in columns:
                    db.execute(statement)
            db.execute(INDEX)
            if "title" not in columns:
                _backfill_summaries(db)

    def get(self, session_id: str, owner_id: str | None = None) -> WorkflowState | None:
        """The session's state; None if it does not exist, belongs to someone else, or cannot be read."""
        stored = self.load(session_id, owner_id)
        return stored.state if stored else None

    def load(self, session_id: str, owner_id: str | None = None) -> StoredSession | None:
        """The session with its version; None if it does not exist or belongs to someone else. An unreadable
        state is logged and comes back as None, so the caller can start it fresh and the next save replaces it."""
        with closing(self._connect()) as db:
            row = db.execute("SELECT state, version FROM sessions WHERE id = ? AND owner_id IS ?",
                             (session_id, owner_id)).fetchone()
        if row is None:
            return None
        try:
            state = WorkflowState.model_validate_json(row[0])
        except ValidationError:
            logger.exception("could not read session %s; starting it fresh", session_id)
            state = None
        return StoredSession(state=state, version=row[1])

    def exists(self, session_id: str) -> bool:
        """Whether any user has a session with this id."""
        with closing(self._connect()) as db:
            return db.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone() is not None

    def save(self, session_id: str, state: WorkflowState, owner_id: str | None = None,
             expected_version: int | None = None) -> bool:
        """Creates or replaces the session. False when the id belongs to another owner, or when
        `expected_version` is given and the session has changed since (0: it must not exist yet)."""
        now = _now()
        with closing(self._connect()) as db, db:  # the inner `db` commits the transaction
            cursor = db.execute(SAVE, (session_id, state.model_dump_json(), now, owner_id, session_title(state),
                                       state.mode, state.workflow is not None, now,
                                       expected_version, expected_version))
            return cursor.rowcount > 0

    def list_sessions(self, owner_id: str, limit: int = 100) -> list[SessionSummary]:
        """The owner's sessions, most recently changed first."""
        with closing(self._connect()) as db:
            rows = db.execute("SELECT id, title, mode, generated, updated_at FROM sessions WHERE owner_id IS ? "
                              "ORDER BY updated_at DESC, id LIMIT ?", (owner_id, limit)).fetchall()
        return [SessionSummary(id=r[0], title=r[1] or "New workflow", mode=r[2] or "collecting",
                               generated=bool(r[3]), updated_at=r[4]) for r in rows]

    def delete(self, session_id: str, owner_id: str | None = None) -> bool:
        with closing(self._connect()) as db, db:
            return db.execute("DELETE FROM sessions WHERE id = ? AND owner_id IS ?",
                              (session_id, owner_id)).rowcount > 0

    def ping(self) -> None:
        """Raises if the database cannot be read; used by the readiness check."""
        with closing(self._connect()) as db:
            db.execute("SELECT 1 FROM sessions LIMIT 1").fetchall()

    def _connect(self) -> sqlite3.Connection:
        # One short connection per call: safe across FastAPI's worker threads without a shared lock.
        # `timeout` makes a writer wait up to 10 s for another writer instead of failing at once.
        return sqlite3.connect(self._path, timeout=10)


def _now() -> str:
    # Milliseconds, so turns saved within the same second still list in order.
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _backfill_summaries(db: sqlite3.Connection) -> None:
    """Fills the list columns of sessions saved before they existed, from each saved state."""
    for session_id, raw, updated_at in db.execute("SELECT id, state, updated_at FROM sessions").fetchall():
        try:
            state = WorkflowState.model_validate_json(raw)
        except ValidationError:
            continue  # unreadable: listed as "New workflow", replaced on its next save
        db.execute("UPDATE sessions SET title = ?, mode = ?, generated = ?, created_at = ? WHERE id = ?",
                   (session_title(state), state.mode, state.workflow is not None, updated_at, session_id))
