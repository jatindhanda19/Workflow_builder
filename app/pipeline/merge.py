"""Merges the planner's proposal into the session. Pure code: the LLM only proposes.

Rules enforced here:
- a value is stored only if the user's own words back it (its evidence) and it validates;
- a stored value changes only when the latest message says so, never by LLM drift;
- an app is set only when the user named or clearly implied it, otherwise it is asked;
- a picked MCQ option is applied to the asked item directly, and a bare typed answer only to the asked item;
- a condition always has a field (a data reference, never a number), an operator and a value;
- the workflow always has one trigger first and at least one action;
- every fill, overwrite, rejection and ignored proposal is written to the log.
"""

import re

from app.pipeline.normalize import (
    EMAIL,
    ORDERING_OPERATORS,
    UNARY_OPERATORS,
    parse_operator,
    plain,
    stated_conditions,
)
from app.pipeline.planner_schema import PlannedParam, PlannedStep, TurnPlan
from app.pipeline.validation import EXPRESSION, FIELD, OPERATOR, VAGUE, VALUE, display, validate
from app.state.models import APP, FieldValue, LogEntry, Param, Step, WorkflowState

CONDITION_APP = "If"
GOAL = "workflow."  # target of the clarifying question asked before anything can be planned
# Built-in nodes the user's wording can imply ("every Monday" → Schedule); any other app must be named.
BUILTIN_APPS = frozenset({"schedule", "webhook", "manual", "if", "http request", "code", "wait", "merge"})
# Kinds whose value must be the user's own words, not a rewording: names, channels, addresses.
LITERAL_KINDS = frozenset({"text", "channel", "email", "email_list", "url"})
# Names the planner may give a condition's parameters; anything else is taken as the field ("amount").
CONDITION_NAMES = {
    FIELD: frozenset({"field", "field_name", "property", "variable", "left", "left_value", "input", "column", "key"}),
    OPERATOR: frozenset({"operator", "comparison", "compare", "condition", "operation", "op"}),
    VALUE: frozenset({"value", "threshold", "right", "right_value", "compare_value", "comparison_value", "limit"}),
}
# "app is X", "use X", "it's X": the words around an app name typed as an answer.
APP_ANSWER_PREFIX = re.compile(
    r"^(?:(?:the|my|our)\s+)?(?:app|application|platform|tool|system|software)\s*(?:is|=|:|-)\s*"
    r"|^(?:use|using|it'?s|it is|via|with|from|in|on)\s+", re.IGNORECASE)
# First words of a typed reply that is not an app name ("not sure", "both", "I use Shopify and …").
NOT_AN_APP = frozenset({"not", "no", "none", "both", "any", "skip", "other", "i", "we", "dont", "don", "what", "why",
                        "how", "which", "can", "yes", "ok", "okay", "change", "instead", "send", "notify", "add",
                        "create", "save", "post", "update", "delete", "remove", "make", "set", "also", "when", "every",
                        "after", "then", "if", "thanks", "thank", "thx", "hi", "hello", "hey", "cool", "great",
                        "nice", "sure", "wait", "stop", "cancel", "help"})
# Words that make a reply a sentence rather than a name ("notify the team", "Shopify and Square").
SENTENCE_WORDS = frozenset({"the", "a", "an", "to", "and", "or", "for", "of", "when", "if", "then", "it", "them", "me",
                            "but", "instead", "please"})
# Words that say nothing about which data a condition checks ("Check condition", "Field to check").
CONDITION_NOISE = frozenset({"check", "checks", "condition", "if", "field", "value", "to", "compare", "with", "the",
                             "is", "whether", "data", "of", "n"})
CONDITION_LABELS = {FIELD: "Field to check", OPERATOR: "Comparison", VALUE: "Value to compare with"}
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
    merger.apply_stated_conditions()
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
        trigger = item.kind == "trigger"
        after = [] if trigger else list(dict.fromkeys(s for s in (_slug(a) for a in item.after) if s and s != step_id))
        step = Step(id=step_id, kind=item.kind, app=app, operation=item.operation.strip() or "Step",
                    after=after, branch=None if trigger else item.branch)
        names: set[str] = set()
        params = _condition_params(item.params) if item.kind == "condition" else item.params
        for planned in params:
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
        if not proposed or (param.value is not None and _same(param.value, proposed)):
            return param  # nothing new: the LLM repeated the stored value
        said =_grounded(planned.evidence, self.user_text) or _mentioned(proposed, self.user_text)
        if not said or (
                param.kind in LITERAL_KINDS and not EXPRESSION.search(proposed) and not _mentioned(proposed, self.user_text)):
            self.log(key, "ignored", f'no evidence for "{proposed}" in what the user said')
            return param
        if self._answers_another_item(key, proposed):
            self.log(key, "ignored", f'"{proposed}" answered {self.state.target}, not this')
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

    def _answers_another_item(self, key: str, proposed: str) -> bool:
        """The latest message is only this value, so it answers the asked item and no other parameter."""
        target = self.state.target or ""
        step_id, _, name = target.partition(".")
        step = self.state.step(step_id)
        asked_param = step is not None and step.param(name) is not None
        return asked_param and target != key and _bare(proposed) == _bare(self.latest)

    def _set(self, key: str, param: Param, verdict, overwritten: bool) -> None:
        if verdict.ok:
            param.value, param.note = verdict.value, None
            self.changed(key, param.label, display(param, verdict.value), overwritten)
        else:
            param.note = verdict.reason
            self.log(key, "rejected", verdict.reason or "")

    # -- picked option ------------------------------------------------------------------------------
    def apply_picked(self) -> None:
        """The option the user picked answers the asked item, whatever the LLM made of it.

        A single typed word answering a condition's field ("#amount", "10000") is read the same way, so a value
        typed where a field was asked is rejected with an explanation instead of being stored anywhere.
        """
        picked, target = self.state.picked, self.state.target
        asked_step = self.state.step((target or "").split(".", 1)[0])
        if not picked and asked_step is not None and not asked_step.app:
            picked = typed_app_answer(self.state)  # the LLM did not take it (it may have called it unclear)
        if not picked and target and "." in target:
            asked = self.state.step(target.split(".", 1)[0])
            param = asked.param(target.split(".", 1)[1]) if asked else None
            typed = self.state.latest_user_message.strip()
            if param is not None and param.kind == "field" and param.value is None and typed and " " not in typed:
                picked = typed
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


    # -- conditions the user wrote ------------------------------------------------------------------
    def apply_stated_conditions(self) -> None:
        """A comparison in the user's words ("if the order total is above ₹50,000") fills the condition in code, so
        its field and comparison are never asked. Restated in the latest message, its comparison and value also
        replace a different reading; the field is only filled, since the LLM may have a better reference for it.
        Each stated condition goes to the condition step it is about ("order value" to check_order_value), never
        by position, so a request with two conditions cannot cross them."""
        conditions = [s for s in self.state.steps if s.kind == "condition"]
        latest = stated_conditions(self.state.latest_user_message)
        earlier = stated_conditions(self.state.user_text())
        for step in conditions:
            stated_now = _stated_for(step, latest, conditions)
            stated = stated_now or _stated_for(step, earlier, conditions)
            if stated and stated[2] and parse_operator(stated[1]) in ORDERING_OPERATORS:
                stated = (stated[0], stated[1], re.sub(r"[^\d.-]", "", stated[2]))  # "₹50,000" → "50000"
            for name, raw in zip((FIELD, OPERATOR, VALUE), stated or ()):
                param = step.param(name)
                verdict = validate(param, raw) if param is not None and raw else None
                if verdict is None or not verdict.ok or _same(param.value, verdict.value):
                    continue
                if name == VALUE and param.value is not None and _digits(param.value) == _digits(raw):
                    continue  # "50000" and "₹50,000" are the same value
                if param.value is None or (stated_now and name != FIELD):
                    self._set(f"{step.id}.{name}", param, verdict, overwritten=param.value is not None)
            value, operator = step.param(VALUE), step.param(OPERATOR)
            if value is not None and operator is not None:
                value.required = operator.value not in UNARY_OPERATORS  # "is missing" compares with nothing


def _stated_for(step: Step, stated: list[tuple], conditions: list[Step]) -> tuple | None:
    """The stated condition about this step's data: its field words appear in the step's id, operation, field
    label or field. One condition and one stated condition belong together when either side names no data
    ("if it is above 500", or a step called "Check condition")."""
    def words(text: str) -> set[str]:
        return set(re.findall(r"[a-z0-9]+", text.lower())) - CONDITION_NOISE

    field = step.param(FIELD)
    about = words(" ".join([step.id, step.operation, field.label if field else "", str(field.value or "") if field else ""]))
    matching = [s for s in stated if s[0] and words(s[0].replace(".", " ").replace("_", " ")) & about]
    if len(matching) == 1:
        return matching[0]
    if not matching and len(stated) == 1 == len(conditions) and (stated[0][0] is None or not about):
        return stated[0]
    return None


def typed_app_answer(state: WorkflowState) -> str | None:
    """The app the user typed when an app was asked ("My Restaurant", "app is Square", "use Zoho"): their own
    system counts too, so it is taken as the answer instead of being asked again."""
    target, typed = state.target or "", state.latest_user_message.strip()
    step = state.step(target.split(".", 1)[0]) if target.endswith(f".{APP}") else None
    chat = state.plan is not None and state.plan.message_kind in ("other", "question", "new_request")
    if step is None or state.picked or chat or "?" in typed:  # "thanks" is not an app
        return None
    name = APP_ANSWER_PREFIX.sub("", typed)
    name = re.sub(r"\s+(?:app|application)$", "", name, flags=re.IGNORECASE).strip(" .!\"'")
    words = plain(name).split()
    if (not words or len(words) > 4 or plain(name) in VAGUE or words[0] in NOT_AN_APP
            or SENTENCE_WORDS & set(words) or not re.search(r"[a-z]", words[0])):
        return None
    return name


def _condition_slot(planned: PlannedParam) -> str | None:
    """field, operator or value by the parameter's name ("comparison_operator", "threshold_value") or kind."""
    name = _slug(planned.name)
    words = set(name.split("_"))
    for slot, names in CONDITION_NAMES.items():
        if name in names:
            return slot
    if planned.kind == "operator" or words & {"operator", "comparison", "compare", "op"}:
        return OPERATOR
    if planned.kind == "field" or words & {"field", "property", "column"}:
        return FIELD
    if words & {"value", "threshold", "limit"}:
        return VALUE
    return None


def _condition_params(params: list[PlannedParam]) -> list[PlannedParam]:
    """Exactly field, operator and value, whatever the planner called them. A parameter named after the data
    ("amount") is the field, so its answer is validated as a field and a number there is rejected."""
    slots: dict[str, PlannedParam] = {}
    unknown = []
    for planned in params:
        slot = _condition_slot(planned)
        if slot and slot not in slots:
            slots[slot] = planned
        else:
            unknown.append(planned)
    if FIELD not in slots and unknown:
        slots[FIELD] = unknown.pop(0)
    result = []
    for slot in (FIELD, OPERATOR, VALUE):
        planned = slots.get(slot)
        label = (planned.label.strip() if planned and slot == FIELD else "") or CONDITION_LABELS[slot]
        kind = {FIELD: "field", OPERATOR: "operator"}.get(slot) or (
            "number" if planned and planned.kind == "number" else "text")
        result.append(PlannedParam(
            name=slot, label=label, description=planned.description if planned else "", kind=kind, required=True,
            value=planned.value if planned else None, evidence=planned.evidence if planned else None))
    return result


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


def _words(text: str) -> list[str]:
    """Whole words for grounding. An email stays one word; elsewhere letters and digits split apart, so "6pm"
    is "6 pm", "₹500" is "500" and "#sales" is "sales"; thousands separators go, so "1,00,000" is "100000"."""
    text = text.lower()
    emails = EMAIL.findall(text)
    rest = EMAIL.sub(" ", text)
    tokens = re.findall(r"[^\W\d_]+|\d+(?:[.,]\d+)*|[<>=≥≤!]+", rest)
    return emails + [re.sub(r",(?=\d)", "", t) for t in tokens]


def _has_words(words: list[str], text: str, plurals: bool = False) -> bool:
    """Every word is a whole word of the text, not part of a longer one ("git" is not in "github")."""
    said = set(_words(text))
    if plurals:  # "Google Sheets" also matches "google sheet"
        said |= {w[:-1] for w in said if w.endswith("s")} | {f"{w}s" for w in said}
    return bool(words) and all(w in said for w in words)


def _grounded(evidence: str | None, text: str) -> bool:
    """Every word of the evidence is a word the user wrote."""
    return bool(evidence and evidence.strip()) and _has_words(_words(evidence), text)


def _app_named(app: str, evidence: str | None, text: str) -> bool:
    """The user named the app itself, or named something only a built-in node does."""
    if app.lower() in BUILTIN_APPS:
        return _grounded(evidence, text)
    return _mentioned(app, text)


def _mentioned(value: str, text: str) -> bool:
    """Every word of the value is a word of the user's text ("Google Sheets" also matches "google sheet")."""
    return _has_words(_words(value), text, plurals=True)


def _bare(text: str) -> str:
    return re.sub(r"\W", "", text.lower())


def _digits(value: FieldValue) -> str:
    return re.sub(r"\D", "", value if isinstance(value, str) else "")


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
