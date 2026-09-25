from app.registry import FIELDS
from app.registry.display import plural
from app.state.models import WorkflowState

CHANNEL_PHRASES = {"email": "an email", "slack": "a Slack message"}
WHEN_PHRASES = {
    "sheet_event": "something changes in your spreadsheet",
    "new_file": "a new file arrives",
    "form_submission": "a form is submitted",
    "manual": "you run it",
}
SOURCE_PHRASES = {
    "form_submission": "new form responses", "sheet_event": "new sheet rows", "new_email": "new emails",
    "new_file": "new files",
}


def acknowledgement(state: WorkflowState, first_question: bool) -> str:
    if first_question:
        return describe_request(state)
    if not state.acks:
        return ""
    if len(state.acks) == 1:
        return f"Got it, {state.acks[0]}."
    return "Got it: " + ", ".join(state.acks) + "."


def describe_request(state: WorkflowState) -> str:
    """One sentence restating the request from what was actually understood."""
    values = state.values()
    goal = values.get("goal.action")
    sentence = _DESCRIBERS.get(str(goal), lambda s, v: "")(state, values)
    return f"Got it: you want {sentence}." if sentence else ""


def _frequency(values: dict) -> str:
    choice = FIELDS["schedule_trigger.frequency"].choice(values.get("schedule_trigger.frequency"))
    return choice.label if choice else "on a schedule"


def _notify(state: WorkflowState, values: dict) -> str:
    entity = state.intent.main_entity
    kind = values.get("trigger.kind")
    when = WHEN_PHRASES.get(str(kind)) or (
        f"a new {entity or 'email'} arrives by email" if kind == "new_email"
        else f"a new {entity} arrives" if entity else "something happens"
    )
    return f"{CHANNEL_PHRASES.get(str(values.get('action.channel')), 'a notification')} when {when}"


def _send_document(state: WorkflowState, values: dict) -> str:
    who = state.intent.recipient_entity
    return f"to send {plural(state.intent.main_entity or 'document')} to {plural(who) if who else 'someone'}"


def _report(state: WorkflowState, values: dict) -> str:
    return f"a {state.intent.main_entity or 'summary'} {_frequency(values)}"


def _backup(state: WorkflowState, values: dict) -> str:
    kind = values.get("trigger.kind")
    if kind not in ("schedule", "manual"):
        entity = state.intent.main_entity
        when = WHEN_PHRASES.get(str(kind)) or ("a new email arrives" if kind == "new_email" else "")
        return f"to save {plural('new ' + entity) if entity else 'files'}{' when ' + when if when else ''}"
    storage = FIELDS["file_source.storage"].choice(values.get("file_source.storage"))
    return f"to back up your {storage.label + ' ' if storage else ''}files {_frequency(values)}"


def _sync(state: WorkflowState, values: dict) -> str:
    source = SOURCE_PHRASES.get(str(values.get("trigger.kind")), "data")
    target = "a spreadsheet" if values.get("action.channel") == "sheet_row" else "another app"
    return f"to copy {source} into {target}"


def _post(state: WorkflowState, values: dict) -> str:
    return f"to post {CHANNEL_PHRASES.get(str(values.get('action.channel')), 'a message')} {_frequency(values)}"


_DESCRIBERS = {
    "notify": _notify, "send_document": _send_document, "report": _report, "backup": _backup,
    "sync_data": _sync, "post_message": _post,
}
