"""Test settings, set before the app is imported: a throwaway signing key, and databases in a temporary folder so
tests never touch sessions.db or auth.db. Values already in the environment win over .env, so these apply."""

import os
import tempfile
from pathlib import Path

_DIR = Path(tempfile.mkdtemp(prefix="workflow-builder-tests-"))
os.environ["AUTH_SECRET_KEY"] = "test-only-signing-key-" + "x" * 32
os.environ["AUTH_DB"] = str(_DIR / "auth.db")
os.environ["SESSION_DB"] = str(_DIR / "sessions.db")
