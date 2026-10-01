"""Merges the planner's proposal into the session. Pure code: the LLM only proposes.

Rules enforced here:
- a value is stored only if the user's own words back it (its evidence) and it validates;
- a stored value changes only when the latest message says so, never by LLM drift;
- an app is set only when the user named or clearly implied it, otherwise it is asked;
- a picked MCQ option is applied to the asked item directly;
- the workflow always has one trigger first and at least one action;
- every fill, overwrite, rejection and ignored proposal is written to the log.
"""

import re

from app.pipeline.normalize import plain
from app.pipeline.planner_schema import PlannedParam, PlannedStep, TurnPlan
from app.pipeline.validation import EXPRESSION, display, validate
from app.state.models import APP, FieldValue, LogEntry, Param, Step, WorkflowState

CONDITION_APP = "If"
GOAL = "workflow."  # target of the clarifying question asked before anything can be planned
# Built-in nodes the user's wording can imply ("every Monday" → Schedule); any other app must be named.
BUILTIN_APPS = frozenset({"schedule", "webhook", "manual", "if", "http request", "code", "wait", "merge"})
# Kinds whose value must be the user's own words, not a rewording: names, channels, addresses.
LITERAL_KINDS = frozenset({"text", "channel", "email", "email_list", "url"})
PLACEHOLDER_TRIGGER = Step(id="trigger", kind="trigger", operation="Start the workflow")
PLACEHOLDER_ACTION = Step(id="action", kind="action", operation="Do something with the result")


def is_placeholder(step: Step) -> bool:
    """A step code added because the plan lacked it; it says nothing about the user's automation yet."""
    return step.app is None and step.operation in (PLACEHOLDER_TRIGGER.operation, PLACEHOLDER_ACTION.operation)


def merge_turn(state: WorkflowState, plan: TurnPlan | None) -> WorkflowState:
    new = state.model_copy(deep=True)
    merger = _Merger(new)
    if plan is not None:
        if plan.workflow_name:
            new.name = plan.workflow_name.strip() or new.name
        if plan.steps:
            new.steps = merger.steps(plan.steps)
    merger.apply_picked()
    asks_about_a_step = plan is not None and plan.next_question is not None and not plan.next_question.target.startswith(GOAL)
    if new.steps or asks_about_a_step:
        # An empty plan that still asks about a step ("trigger.app") starts from trigger → action placeholders.
        new.steps = _structured(new.steps)
    # A generated workflow is kept across turns; check() regenerates it once the changed draft is complete.
    return new


class _Merger:
    def __init__(self, state: WorkflowState) -> None:
        self.state = state
        self.user_text = state.user_text().lower()
        self.latest = state.latest_user_message.lower()

    def log(self, key: str, event: str, detail: str = "") -> None:
        self.state.log.append(LogEntry(turn=self.state.turn, field=key, event=event, detail=detail))  # type: ignore[arg-type]

    def changed(self, key: str, label: str, value: FieldValue, overwritten: bool, announce: bool = True) -> None:
        self.log(key, "overwritten" if overwritten else "filled", display(None, value))
        if announce:
            self.state.changes.append(f"{label}: {display(None, value)}")

    # -- steps --------------------------------------------------------------------------------------
    def steps(self, planned: list[PlannedStep]) -> list[Step]:
        old_steps = list(self.state.steps)
        used: set[str] = set()
        result: list[Step] = []
        for item in planned:
            step_id = _unique(_slug(item.id) or f"step_{len(result) + 1}", {s.id for s in result})
            old = next((s for s in old_steps if s.id == step_id and s.id not in used), None) or next(
                (s for s in old_steps if s.id not in used and s.kind == item.kind and s.app and item.app
                 and s.app.lower() == item.app.lower()), None)
            if old:
                used.add(old.id)
            result.append(self._step(step_id, item, old))
        return result

    def _step(self, step_id: str, item: PlannedStep, old: Step | None) -> Step:
        app = self._app(step_id, item, old)
        step = Step(id=step_id, kind=item.kind, app=app, operation=item.operation.strip() or "Step")
        names: set[str] = set()
        for planned in item.params:
            name = _slug(planned.name)
            if not name or name in names or name == APP:
                continue
            names.add(name)
            step.params.append(self._param(step_id, planned, old.param(name) if old else None))
        # Values the user already gave stay, even if the LLM left their parameter out this turn.
        if old and old.app == app:
            step.params += [p for p in old.params if p.name not in names and p.value is not None]
        return step

    def _app(self, step_id: str, item: PlannedStep, old: Step | None) -> str | None:
        key = f"{step_id}.{APP}"
        proposed = (item.app or "").strip() or None
        if item.kind == "condition":
            return CONDITION_APP
        if old and old.app:
            if proposed and proposed.lower() != old.app.lower():
                if _app_named(proposed, item.app_evidence, self.latest):
                    self.changed(key, "App", proposed, overwritten=True)
                    return proposed
                self.log(key, "ignored", f"kept {old.app}; {proposed} was not asked for")
            return old.app
        if proposed and _app_named(proposed, item.app_evidence, self.user_text):
            self.changed(key, "App", proposed, overwritten=False, announce=False)
            return proposed
        if proposed:
            self.log(key, "ignored", f'"{proposed}" was not named by the user')
        return None

    def _param(self, step_id: str, planned: PlannedParam, old: Param | None) -> Param:
        key = f"{step_id}.{_slug(planned.name)}"
        param = Param(
            name=_slug(planned.name), label=planned.label.strip() or planned.name, description=planned.description,
            kind=planned.kind, choices=[c for c in planned.choices if c.strip()] if planned.kind == "choice" else [],
            required=planned.required,
        )
        if old is not None:
            kept = validate(param, old.value) if old.value is not None else None
            param.value = kept.value if kept and kept.ok else None
            param.note = None if param.value is not None else old.note
        proposed = (planned.value or "").strip()
        if not proposed:
            return param
        said = _grounded(planned.evidence, self.user_text) or _mentioned(proposed, self.user_text)
        if not said or (
                param.kind in LITERAL_KINDS and not EXPRESSION.search(proposed) and not _mentioned(proposed, self.user_text)):
            self.log(key, "ignored", f'no evidence for "{proposed}" in what the user said')
            return param
        verdict = validate(param, proposed)
        if param.value is not None:
            if not verdict.ok or _same(param.value, verdict.value):
                return param
            if not (_grounded(planned.evidence, self.latest) or _mentioned(proposed, self.latest)):
                self.log(key, "ignored", f"kept {display(param, param.value)}; the latest message did not change it")
                return param
        self._set(key, param, verdict, overwritten=param.value is not None)
        return param

    def _set(self, key: str, param: Param, verdict, overwritten: bool) -> None:
        if verdict.ok:
            param.value, param.note = verdict.value, None
            self.changed(key, param.label, display(param, verdict.value), overwritten)
        else:
            param.note = verdict.reason
            self.log(key, "rejected", verdict.reason or "")

    # -- picked option ------------------------------------------------------------------------------
    def apply_picked(self) -> None:
        """The option the user picked answers the asked item, whatever the LLM made of it."""
        picked, target = self.state.picked, self.state.target
        if not picked or not target or "." not in target:
            return
        step_id, name = target.split(".", 1)
        step = self.state.step(step_id)
        if step is None:
            return
        if name == APP:
            if (step.app or "").lower() != picked.lower():
                self.changed(target, "Trigger" if step.kind == "trigger" else "App", picked, overwritten=bool(step.app))
                step.app = picked
            return
        param = step.param(name)
        if param is None:
            return
        verdict = validate(param, picked)
        if verdict.ok and param.value is not None and _same(param.value, verdict.value):
            return
        self._set(target, param, verdict, overwritten=param.value is not None)


def _structured(steps: list[Step]) -> list[Step]:
    """One trigger, first, and at least one action. Missing parts become steps whose app is asked."""
    triggers = [s for s in steps if s.kind == "trigger"]
    others = [s for s in steps if s.kind != "trigger"]
    trigger = triggers[0] if triggers else PLACEHOLDER_TRIGGER.model_copy(deep=True)
    if not any(s.kind == "action" for s in others):
        others.append(PLACEHOLDER_ACTION.model_copy(deep=True))
    ordered = [trigger, *others]
    seen: set[str] = set()
    for step in ordered:
        step.id = _unique(step.id, seen)
        seen.add(step.id)
    return ordered


def _grounded(evidence: str | None, text: str) -> bool:
    """Every word of the evidence appears in what the user wrote."""
    if not evidence or not evidence.strip():
        return False
    words = re.findall(r"[\w@#₹$€£+-]+", evidence.lower())
    return bool(words) and all(w in text for w in words)


def _app_named(app: str, evidence: str | None, text: str) -> bool:
    """The user named the app itself, or named something only a built-in node does."""
    if app.lower() in BUILTIN_APPS:
        return _grounded(evidence, text)
    return _mentioned(app, text)


def _mentioned(value: str, text: str) -> bool:
    """Every word of the value appears in the user's text ("Google Sheets" also matches "google sheet")."""
    words = re.findall(r"[\w@.+-]+", value.lower())
    return bool(words) and all(w in text or w.rstrip("s") in text for w in (w.strip(".") for w in words) if w)


def _same(a: FieldValue | None, b: FieldValue | None) -> bool:
    def norm(v: FieldValue | None) -> object:
        return sorted(plain(x) for x in v) if isinstance(v, list) else plain(v or "")

    return norm(a) == norm(b)


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return f"n_{slug}" if slug and slug[0].isdigit() else slug


def _unique(base: str, taken: set[str]) -> str:
    candidate, index = base, 2
    while candidate in taken:
        candidate, index = f"{base}_{index}", index + 1
    return candidate
