"""One JSON line per conversation turn: what the model proposed, and what code kept, rejected or ignored and why.

    {"event": "turn", "session": "…", "turn": 3, "latency_ms": 812, "unwrapped": false, "message_kind": "build",
     "proposed": [{"step": "check_amount", "kind": "condition", "app": "If", "values": {"value": "100000"}}],
     "kept": [{"field": "check_amount.value", "value": "100000"}],
     "rejected": [{"field": "check_amount.field", "reason": "… 10000 looks like a value, not a field name"}],
     "ignored": [{"field": "trigger.app", "reason": "\\"Git\\" was not named by the user"}],
     "asked": "check_amount.field", "mode": "collecting", "generated": false}

The lines go to the "app.turns" logger on stderr, so `grep '"event": "turn"'` or any log tool can read them.
Values the user typed are logged with emails, phone numbers and ID numbers replaced ("[EMAIL]"), so logs hold no
personal data.
"""

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from app.pipeline.gaps import completeness_gaps
from app.security.pii import redact_pii
from app.state.models import WorkflowState

logger = logging.getLogger("app.turns")
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(message)s"))  # the line itself is the JSON record
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

KEPT = ("filled", "overwritten")


def turn_record(session_id: str, before: WorkflowState, after: WorkflowState, latency_ms: int) -> dict[str, Any]:
    plan = after.plan
    entries = [e for e in after.log if e.turn == after.turn]
    return {
        "event": "turn",
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "session": session_id,
        "turn": after.turn,
        "latency_ms": latency_ms,
        "picked": _clean(after.picked),
        "llm_failed": after.llm_failed,
        # The model wrapped its reply in the JSON schema and code unwrapped it (see app/core/llm/client.py).
        "unwrapped": bool((after.llm_call or {}).get("unwrapped")),
        "message_kind": plan.message_kind if plan else None,
        "proposed": [
            {"step": s.id, "kind": s.kind, "app": s.app,
             **({"after": s.after} if s.after else {}), **({"branch": s.branch} if s.branch else {}),
             "values": {p.name: _clean(p.value) for p in s.params if p.value is not None}}
            for s in (plan.steps if plan else [])
        ],
        "kept": [{"field": e.field, "value": _clean(e.detail)} for e in entries if e.event in KEPT],
        "rejected": [{"field": e.field, "reason": _clean(e.detail)} for e in entries if e.event == "rejected"],
        "ignored": [{"field": e.field, "reason": _clean(e.detail)} for e in entries if e.event == "ignored"],
        "asked": after.target if after.question else None,
        # What still keeps the workflow from being complete (app/pipeline/gaps.py).
        "gaps": [_clean(g) for g in completeness_gaps(after)],
        "mode": after.mode,
        "generated": after.workflow is not None and after.workflow != before.workflow,
    }


def _clean(value: Any) -> Any:
    if isinstance(value, str):
        return redact_pii(value)
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def log_turn(session_id: str, before: WorkflowState, after: WorkflowState, latency_ms: int) -> None:
    logger.info(json.dumps(turn_record(session_id, before, after, latency_ms), ensure_ascii=False, default=str))
