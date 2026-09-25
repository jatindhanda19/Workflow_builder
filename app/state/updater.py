"""Merges one user message into the collected-fields store. Pure code: the LLM only proposes values.

Rules enforced here:
- a value is stored only after it validates and passes the consistency checks;
- a filled field is never overwritten silently: a different value opens a conflict question
  (edits after generation are explicit, so they overwrite and are logged);
- an unusable value never clears a filled field;
- every fill, ambiguity, conflict, derivation and clear is written to the log.
"""

import re

from app.pipeline.extraction.normalize import EMAIL, normalize_text
from app.pipeline.extraction.schema import Extraction
from app.pipeline.intent.rules import direction_for
from app.pipeline.intent.schema import IntentResult
from app.pipeline.planning import build_plan
from app.registry import FIELDS, YES, FieldDef, get_field, selected_node_for
from app.registry.display import display_value, echo_text
from app.state.models import Conflict, FieldEntry, FieldValue, LogEntry, Option, WorkflowState
from app.pipeline.validation.consistency import CheckContext, detect, resolve
from app.pipeline.validation.fields import same_value, validate

REPLACE_WORDS = frozenset(YES) | {"replace", "new", "change", "update"}
SHORT_ANSWER_WORDS = 6
SELECTORS = ("trigger.kind", "action.channel")
MONEY = re.compile(r"[₹$€£]|\b(?:rs|inr|usd|eur|gbp)\b\.?\s*\d", re.IGNORECASE)
CONDITION_MODES = {"sheet_event": ("sheet_trigger.condition_mode", "specific_value"),
                   "new_email": ("email_trigger.condition_mode", "matching_condition")}


class Merge:
    def __init__(self, state: WorkflowState, overwrite: bool) -> None:
        self.state = state
        self.message = state.latest_user_message
        self.overwrite = overwrite
        self.touched: set[str] = set()

    # -- store helpers -----------------------------------------------------------------
    def log(self, key: str, event: str, detail: str = "") -> None:
        self.state.log.append(LogEntry(turn=self.state.turn, field=key, event=event, detail=detail))

    def fill(self, fdef: FieldDef, value: FieldValue, source: str = "user", event: str = "filled") -> None:
        self.state.fields[fdef.key] = FieldEntry(key=fdef.key, status="filled", value=value, source=source)
        self.touched.add(fdef.key)
        self.log(fdef.key, event, _show(value))
        self.state.changes.append(f"{fdef.label}: {display_value(fdef, value)}")
        echo = echo_text(fdef, value)
        if source == "user" and echo:
            self.state.acks.append(echo)
        choice = fdef.choice(value) if fdef.choices else None
        for key, implied in choice.implies if choice else ():
            if key not in self.state.fields:
                self.fill(FIELDS[key], implied, source="derived", event="derived")

    def clear(self, key: str) -> None:
        if self.state.fields.pop(key, None) is not None:
            self.log(key, "cleared")
        self.touched.add(key)

    def mark_ambiguous(self, key: str, phrase: str, reason: str, question: str | None = None,
                       options: list[Option] | None = None) -> None:
        self.state.fields[key] = FieldEntry(
            key=key, status="ambiguous", phrase=phrase, reason=reason, question=question, options=options or [],
        )
        self.touched.add(key)
        self.log(key, "ambiguous", reason)

    def assign(self, values: dict[str, FieldValue | None]) -> None:
        for key, value in values.items():
            if key == "intent.cardinality":
                self.state.intent.cardinality = value  # type: ignore[assignment]
            elif value is None:
                self.clear(key)
            else:
                self.fill(FIELDS[key], value, event="overwritten" if self.state.is_filled(key) else "filled")

    def check_context(self) -> CheckContext:
        intent = self.state.intent
        return CheckContext(
            message=self.message, target_field=self.state.target_field, values=self.state.values(),
            original_request=self.state.original_request or "", cardinality=intent.cardinality,
            recipient_word=_singular(intent.recipient_entity or "recipient"),
        )

    # -- steps ---------------------------------------------------------------------------
    def apply_intent(self, result: IntentResult) -> None:
        intent = self.state.intent
        goal = FIELDS["goal.action"]
        if result.goal_action and goal.key not in self.state.fields:
            self.fill(goal, result.goal_action)
        intent.direction = intent.direction or result.direction or direction_for(result.goal_action)
        intent.entities = list(dict.fromkeys([*intent.entities, *(_singular(e) for e in result.entities)]))
        intent.data_sources = list(dict.fromkeys([*intent.data_sources, *result.data_sources]))
        intent.cardinality = intent.cardinality or result.recipient_cardinality
        kind = FIELDS["trigger.kind"]
        if result.trigger_hint and kind.key not in self.state.fields:
            self.fill(kind, result.trigger_hint)
            self.cascade(kind.key, result.trigger_evidence or self.message)
        channel = FIELDS["action.channel"]
        if result.channel_hint and channel.key not in self.state.fields:
            self.fill(channel, result.channel_hint)

    def resolve_conflict(self) -> None:
        if self.state.target_kind != "conflict" or not self.state.conflicts:
            return
        conflict = self.state.conflicts.pop(0)
        words = set(re.findall(r"[a-z']+", self.message.lower()))
        if words & REPLACE_WORDS and not words & {"keep", "old", "no"}:
            self.fill(FIELDS[conflict.key], conflict.new, event="overwritten")
        else:
            self.touched.add(conflict.key)
            self.log(conflict.key, "kept", _show(conflict.old))

    def resolve_target_options(self) -> None:
        entry = self.state.fields.get(self.state.target_field or "")
        if entry is None or entry.status != "ambiguous" or not entry.options:
            return
        chosen = resolve(entry.options, self.message)
        if chosen is not None:
            self.touched.add(entry.key)
            self.assign(chosen)

    def apply_extraction(self, extraction: Extraction) -> None:
        for item in extraction.values:
            fdef = get_field(item.field)
            if fdef is None or item.field in self.touched:
                continue
            if not _evidence_ok(item.evidence, self.message):
                self.log(item.field, "ignored", f'no evidence for "{item.value}" in the message')
                continue
            self.offer(fdef, item.value)
            if fdef.key in SELECTORS and self.state.is_filled(fdef.key):
                self.cascade(fdef.key, item.evidence)
        for flag in extraction.ambiguities:
            fdef = get_field(flag.field)
            if fdef is None or flag.field in self.touched or self.state.is_filled(flag.field):
                continue
            if _evidence_ok(flag.phrase, self.message):
                options = [
                    Option(label=text, assign={fdef.key: verdict.value}, keywords=[text.lower()])
                    for text in flag.interpretations
                    if (verdict := validate(fdef, text)).ok and verdict.value is not None
                ]
                self.mark_ambiguous(fdef.key, flag.phrase, flag.reason, options=options)

    def apply_direct_answer(self, extracted_anything: bool) -> None:
        """Handle the reply to the question just asked when the extractor did not cover it."""
        key = self.state.target_field
        if not key or key in self.touched or self.state.target_kind == "conflict" or key not in FIELDS:
            return
        if key == "recipients.source" and EMAIL.search(self.message):
            self.offer(FIELDS["email.recipients"], self.message)
            if self.state.is_filled("email.recipients"):
                self.fill(FIELDS[key], "fixed_list")
            return
        fdef = FIELDS[key]
        verdict = validate(fdef, self.message, self.state.values())
        if not verdict.ok and self.switches_selector(key):
            return
        short = len(self.message.split()) <= SHORT_ANSWER_WORDS
        free = fdef.kind in ("free_text", "field_list", "mapping")
        if verdict.ok and fdef.kind in ("text", "name") and (extracted_anything or not short):
            return
        if verdict.ok or (short and not extracted_anything) or (free and not extracted_anything):
            self.offer(fdef, self.message)
            if key in SELECTORS and self.state.is_filled(key):
                self.cascade(key, self.message)

    def switches_selector(self, asked_key: str) -> bool:
        """An answer that doesn't fit the question but names another trigger or channel opens a conflict."""
        for key in SELECTORS:
            if key == asked_key or not self.state.is_filled(key):
                continue
            verdict = validate(FIELDS[key], self.message)
            if verdict.ok and not same_value(verdict.value, self.state.values()[key]):
                self.offer(FIELDS[key], self.message)
                return True
        return False

    def cascade(self, selector_key: str, text: str) -> None:
        """ "Gmail" answers both trigger.kind (new email) and the mailbox provider."""
        node = selected_node_for(selector_key, self.state.values().get(selector_key))
        first_choice = next((fd for fd in node.fields if fd.choices), None) if node else None
        if first_choice and first_choice.key not in self.state.fields:
            verdict = validate(first_choice, text)
            if verdict.ok and verdict.value is not None:
                self.fill(first_choice, verdict.value)

    def offer(self, fdef: FieldDef, raw: str) -> None:
        entry = self.state.fields.get(fdef.key)
        verdict = validate(fdef, raw, self.state.values())
        if not verdict.ok:
            if entry is not None and entry.status == "filled" and not self.overwrite:
                self.log(fdef.key, "ignored", f"kept {_show(entry.value)}; new input unusable: {verdict.reason}")
                return
            reason = verdict.reason or "I could not use that answer"
            question = f"{reason}. {verdict.question}" if verdict.question else None
            self.mark_ambiguous(fdef.key, normalize_text(raw), reason, question, verdict.options)
            return
        value = verdict.value
        assert value is not None
        if entry is not None and entry.status == "filled":
            self._offer_to_filled(fdef, entry, value)
            return
        flagged = detect(fdef, value, self.check_context())
        if flagged:
            self.mark_ambiguous(flagged.key, flagged.phrase, flagged.reason, flagged.question, flagged.options)
            return
        self.fill(fdef, value)

    def _offer_to_filled(self, fdef: FieldDef, entry: FieldEntry, value: FieldValue) -> None:
        self.touched.add(fdef.key)
        if same_value(entry.value, value):
            return
        if self.overwrite:
            self.fill(fdef, value, event="overwritten")
        elif not any(c.key == fdef.key for c in self.state.conflicts):
            self.state.conflicts.append(Conflict(key=fdef.key, old=entry.value, new=value))  # type: ignore[arg-type]
            self.log(fdef.key, "conflict", f"{_show(entry.value)} vs {_show(value)}")

    def derive(self) -> None:
        values = self.state.values()
        if "email.body_text" in values and "email.content_mode" not in self.state.fields:
            self.fill(FIELDS["email.content_mode"], "fixed_text", source="derived", event="derived")
        if "template.body" in values and "email.content_mode" not in self.state.fields:
            self.fill(FIELDS["email.content_mode"], "template", source="derived", event="derived")
        self._derive_condition()

    def _derive_condition(self) -> None:
        cardinality = self.state.intent.cardinality
        values = self.state.values()
        mode_key, conditional = CONDITION_MODES.get(str(values.get("trigger.kind")), (None, None))
        condition_given = any(k.startswith("condition.") for k in values)
        if mode_key and conditional and condition_given and mode_key not in self.state.fields:
            if any(fd.key == mode_key for fd in build_plan(values, cardinality).fields):
                self.fill(FIELDS[mode_key], conditional, source="derived", event="derived")
        values = self.state.values()
        if build_plan(values, cardinality).node("condition") is None:
            return
        if "condition.field" not in self.state.fields and MONEY.search(str(values.get("condition.value", ""))):
            self.fill(FIELDS["condition.field"], "amount", source="derived", event="derived")
        if values.get("trigger.kind") != "sheet_event":
            return
        column = values.get("sheet_trigger.watched_column")
        if column and "condition.field" not in self.state.fields:
            self.fill(FIELDS["condition.field"], column, source="derived", event="derived")
        if "condition.value" in values and "condition.operator" not in self.state.fields:
            self.fill(FIELDS["condition.operator"], "equals", source="derived", event="derived")


def merge_turn(
    state: WorkflowState,
    intent: IntentResult | None,
    extraction: Extraction | None,
    overwrite: bool = False,
    fallback_values: dict[str, str] | None = None,
) -> WorkflowState:
    new = state.model_copy(deep=True)
    before = new.values()
    merge = Merge(new, overwrite)
    merge.resolve_conflict()
    merge.resolve_target_options()
    if intent is not None:
        merge.apply_intent(intent)
    extracted_anything = bool(extraction and (extraction.values or extraction.ambiguities))
    if extraction is not None:
        merge.apply_extraction(extraction)
    merge.apply_direct_answer(extracted_anything)
    for key, raw in (fallback_values or {}).items():
        if key in FIELDS and key not in merge.touched:
            merge.offer(FIELDS[key], raw)
    merge.derive()
    if new.values() != before:
        new.workflow = None
    return new


def reopen(state: WorkflowState, keys: list[str]) -> list[str]:
    reopened = [key for key in keys if state.fields.pop(key, None) is not None]
    for key in reopened:
        state.log.append(LogEntry(turn=state.turn, field=key, event="reopened"))
    if reopened:
        state.workflow = None
    return reopened


def _evidence_ok(evidence: str | None, message: str) -> bool:
    if not evidence or not evidence.strip():
        return False
    words = re.findall(r"[\w@#₹$€£.]+", evidence.lower())
    haystack = message.lower()
    return bool(words) and all(w.strip(".") in haystack for w in words)


def _singular(word: str) -> str:
    word = word.lower().strip()
    return word[:-1] if word.endswith("s") and not word.endswith("ss") else word


def _show(value: FieldValue | None) -> str:
    return ", ".join(value) if isinstance(value, list) else (value or "")
