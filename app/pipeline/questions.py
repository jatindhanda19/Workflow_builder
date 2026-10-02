"""The next question, always multiple choice (MCQ).

The planner LLM predicts which missing item to ask and the likely answers; code checks that the item
really is missing, keeps only options that validate, and builds a plain question when the LLM's is unusable.
"""

import re
import string

from app.pipeline.merge import is_placeholder
from app.pipeline.normalize import plain
from app.pipeline.planner_schema import NextQuestion
from app.pipeline.validation import OpenItem, display, open_items, validate
from app.state.models import APP, Question, QuestionOption, WorkflowState

MAX_OPTIONS = 5
MAX_QUESTION_LENGTH = 300
LETTERS = string.ascii_uppercase
STILL_NEED = "I still need this before I can continue."
ACK_PREFIX = re.compile(
    r"^(?:got it|thanks|thank you|great|perfect|okay|ok|sure|noted|understood|alright)\b[^?]*?[.!,:]\s*",
    re.IGNORECASE,
)
PICK = re.compile(r"^\(?([a-z]|\d{1,2})[).:]?$")
# "Other" / "Custom message" add nothing: typing your own answer is always possible.
CATCH_ALL = re.compile(r"^(?:other|custom|something else|none of these)\b", re.IGNORECASE)
GOAL_TARGET = "workflow.goal"
CLARIFY_TARGET = "clarify"
CLARIFY_QUESTION = "I'm not sure what you mean. Which part of the workflow should this change?"
GOAL_QUESTION = "What would you like to automate?"
GOAL_EXAMPLES = (
    "When a new order arrives in Shopify, post it to a Slack channel",
    "Every Monday at 9 AM, email me a summary from Google Sheets",
    "When a form is submitted, add the response to Airtable and email the person",
    "When a GitHub issue is opened, create a Jira ticket",
)
# Used when the LLM suggests nothing usable, so a question still has choices.
DEFAULT_OPTIONS: dict[str, tuple[str, ...]] = {
    "time": ("9:00 AM", "12:00 PM", "6:00 PM"),
    "timezone": ("Asia/Kolkata", "UTC", "Europe/London", "America/New_York"),
}
DEFAULT_APPS = {
    "trigger": ("Schedule", "Webhook", "Gmail", "Google Forms", "Manual"),
    "action": ("Gmail", "Slack", "Google Sheets", "Microsoft Teams", "HTTP Request"),
}
NEW_WORKFLOW_YES = "Yes, start a new workflow"
NEW_WORKFLOW_NO = "No, keep the current one"


def next_question(state: WorkflowState, predicted: NextQuestion | None) -> tuple[str, Question] | None:
    if not state.steps:
        return GOAL_TARGET, _goal_question(predicted)
    items = open_items(state)
    if not items:
        return None
    # Questions follow the automation: only items of the earliest unfinished step can be asked.
    current = [i for i in items if i.step.id == items[0].step.id]
    item, use_ai = _resolve(state, current, predicted)
    text = _clean(predicted.question) if use_ai and predicted else ""
    text = text or _fallback_text(item)
    note = (item.param.note if item.param else None) or (STILL_NEED if state.asked_count(item.key) >= 1 else None)
    options = _options(item, predicted.options if use_ai and predicted else [], state.user_text().lower())
    choice_only = item.param is not None and item.param.kind == "choice" and bool(item.param.choices)
    return item.key, Question(target=item.key, step=_step_context(state, item), text=text, note=note,
                              options=options, allow_custom=not choice_only)


def _resolve(state: WorkflowState, current: list[OpenItem], predicted: NextQuestion | None) -> tuple[OpenItem, bool]:
    """The item to ask, and whether the AI's question and options are about it."""
    if predicted is None:
        return current[0], False
    target = predicted.target.strip()
    exact = next((i for i in current if i.key == target), None)
    if exact:
        return exact, True
    # The AI may name a step that is still a placeholder ("email_trigger.app" for "trigger.app").
    step_id, _, name = target.partition(".")
    if name == APP and current[0].param is None and state.step(step_id) is None:
        return current[0], True
    return current[0], False


def clarify_question(state: WorkflowState, predicted: NextQuestion | None) -> Question:
    """The latest message has several readings: the AI's question naming them, or a plain one listing the steps."""
    text = _clean(predicted.question) if predicted else ""
    options = _dedupe([o.strip() for o in predicted.options if not CATCH_ALL.match(o.strip())]) if predicted else []
    if text and len(options) >= 2:
        return Question(target=CLARIFY_TARGET, text=text, options=options)
    steps = _dedupe([s.title for s in state.steps if not is_placeholder(s)])
    return Question(target=CLARIFY_TARGET, text=text or CLARIFY_QUESTION, options=options if len(options) >= 2 else steps)


def confirm_new_request_question(text: str) -> Question:
    return Question(text=text, options=[QuestionOption(label=NEW_WORKFLOW_YES), QuestionOption(label=NEW_WORKFLOW_NO)],
                    allow_custom=False)


def match_option(question: Question | None, message: str) -> str | None:
    """The option a reply picks: its exact label, its letter or its number."""
    if question is None or not question.options:
        return None
    text = plain(message)
    exact = next((o.label for o in question.options if plain(o.label) == text), None)
    pick = PICK.match(text)
    if exact or not pick:
        return exact
    token = pick.group(1)
    index = int(token) - 1 if token.isdigit() else LETTERS.index(token.upper())
    return question.options[index].label if 0 <= index < len(question.options) else None


def render(question: Question) -> str:
    """The question as chat text. The UI shows the options as buttons, so they are not repeated here."""
    text = f"{question.note.rstrip('.')}. {question.text}" if question.note else question.text
    return f"_{question.step}_\n\n{text}" if question.step else text


def _options(item: OpenItem, suggested: list[str], user_text: str) -> list[QuestionOption]:
    param = item.param
    suggested = [s for s in suggested if not CATCH_ALL.match(s.strip())]
    if param is not None and param.kind in ("email", "email_list"):
        # Only addresses the user wrote: an invented address is a guess.
        suggested = [s for s in suggested if "{{" in s or s.strip().lower() in user_text]
    if param is None:
        apps = [s.strip() for s in suggested if s.strip()]
        if len(apps) < 2:
            apps += DEFAULT_APPS["trigger" if item.step.kind == "trigger" else "action"]
        return _dedupe(apps)
    if param.kind == "choice" and param.choices:
        # Every choice is listed: a choice parameter accepts nothing else.
        ranked = [c for s in suggested for c in param.choices if plain(c) == plain(s)]
        return _dedupe([*ranked, *param.choices], limit=None)
    # What the rejected answer may have meant ("evening": 5, 6 or 7 PM) comes first.
    labels = _valid_labels(param, [*param.guesses, *suggested])
    if len(labels) < 2:
        labels += _valid_labels(param, list(DEFAULT_OPTIONS.get(param.kind, ())))
    return _dedupe(labels)


def _valid_labels(param, raw_values: list[str]) -> list[str]:
    labels = []
    for raw in raw_values:
        verdict = validate(param, raw)
        if verdict.ok and verdict.value is not None:
            labels.append(raw.strip() if param.kind in ("text", "long_text") else display(param, verdict.value))
    return labels


def _goal_question(predicted: NextQuestion | None) -> Question:
    """Nothing could be planned yet: the AI's clarifying question, or general examples."""
    if predicted is not None and predicted.target.strip() == GOAL_TARGET:
        text = _clean(predicted.question)
        options = _dedupe([o.strip() for o in predicted.options if o.strip()])
        if text and len(options) >= 2:
            return Question(target=GOAL_TARGET, text=text, options=options)
    return Question(target=GOAL_TARGET, text=GOAL_QUESTION, options=[QuestionOption(label=e) for e in GOAL_EXAMPLES])


def _fallback_text(item: OpenItem) -> str:
    operation = item.step.operation[:1].lower() + item.step.operation[1:]
    if item.param is None:
        if item.step.kind == "trigger":
            return f'Which app should I watch for "{operation}"?'
        return f"Which app should I use to {operation}?"
    hint = f" ({item.param.description.rstrip('.')})" if item.param.description else ""
    return f"What should the {item.param.label.lower()} be{hint}?"


def _step_context(state: WorkflowState, item: OpenItem) -> str | None:
    """E.g. "Step 1 of 3 · Trigger: Gmail: New email"."""
    if all(is_placeholder(s) for s in state.steps):
        return None
    position = state.steps.index(item.step) + 1
    role = {"trigger": "Trigger", "condition": "Condition", "action": "Action"}[item.step.kind]
    return f"Step {position} of {len(state.steps)} · {role}: {item.step.title}"


def _clean(text: str | None) -> str:
    text = ACK_PREFIX.sub("", (text or "").strip()).strip()
    text = text[:1].upper() + text[1:]
    ok = text and "?" in text and len(text) <= MAX_QUESTION_LENGTH and text.count("?") <= 2
    return text if ok else ""


def _dedupe(labels: list[str], limit: int | None = MAX_OPTIONS) -> list[QuestionOption]:
    seen: set[str] = set()
    options = []
    for label in labels:
        if plain(label) and plain(label) not in seen:
            seen.add(plain(label))
            options.append(QuestionOption(label=label))
        if len(options) == limit:
            break
    return options
