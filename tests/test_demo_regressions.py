"""Regressions found in a recorded demo run.

The demo ran while the LLM provider was rate-limited, so every extraction call failed
and the pipeline fell back to rules. Each test below replays one failure from it.
"""

from app.core.llm.client import LLMClient, LLMError
from app.pipeline.extraction.schema import Extraction
from tests.conftest import Conversation, v

REQUEST = "Send an email when something happens in my sheet"
SHEET_INTENT = {"goal_action": "notify", "trigger_hint": "sheet_event", "trigger_evidence": "in my sheet"}


def say_offline(convo: Conversation, message: str) -> None:
    """Send a message while every LLM call fails, as it did during the demo."""
    convo.llm.failing.add(message)
    convo.say(message)


# --------------------------------------------------------------------------- stated channel
def test_email_in_the_request_survives_an_llm_outage(convo: Conversation) -> None:
    say_offline(convo, REQUEST)
    assert convo.value("action.channel") == "email"
    assert convo.reply.startswith("Got it: you want an email when something changes in your spreadsheet.")
    assert convo.asking == "sheet_trigger.platform"


def test_email_in_the_request_is_kept_when_the_llm_misses_it(convo: Conversation) -> None:
    convo.say(REQUEST, extraction={}, intent=SHEET_INTENT)
    assert convo.value("action.channel") == "email"
    assert "action.channel" not in convo.asked()


def test_an_incoming_email_is_not_mistaken_for_the_channel(convo: Conversation) -> None:
    say_offline(convo, "When a new email arrives in my inbox, notify me")
    assert convo.value("trigger.kind") == "new_email"
    assert convo.value("action.channel") is None


def test_changing_the_stated_channel_asks_before_replacing_it(convo: Conversation) -> None:
    say_offline(convo, REQUEST)
    convo.say("save files to another folder", {"values": [v("action.channel", "save_file", "save files")]})
    assert convo.value("action.channel") == "email"
    assert convo.state.target_kind == "conflict"
    assert convo.reply.startswith('Earlier you said the action channel is "by email", but now "save files to another folder".')


def test_changing_the_channel_mid_conversation_is_caught_without_the_llm(convo: Conversation) -> None:
    for message in (REQUEST, "Google sheet", "daily_invoices", "Sheet1", "any cell is updated"):
        say_offline(convo, message)
    assert convo.asking == "email.provider"
    say_offline(convo, "save files to another folder")
    assert convo.state.target_kind == "conflict"
    assert convo.value("action.channel") == "email"
    say_offline(convo, "yes")
    assert convo.value("action.channel") == "save_file"
    assert convo.asking == "save_file.storage"


# --------------------------------------------------------------------------- misplaced answers
def open_sheet(convo: Conversation) -> None:
    say_offline(convo, REQUEST)
    say_offline(convo, "Google sheet")
    say_offline(convo, "daily_invoices")
    assert convo.asking == "sheet_trigger.tab_name"


def test_an_event_phrase_given_as_the_tab_name_is_confirmed(convo: Conversation) -> None:
    open_sheet(convo)
    say_offline(convo, "change in value")
    assert convo.value("sheet_trigger.tab_name") is None
    assert convo.reply.startswith('Just to check: is "change in value" the exact name of the tab?')
    say_offline(convo, "no")
    assert convo.asking == "sheet_trigger.tab_name"
    say_offline(convo, "Sheet1")
    assert convo.value("sheet_trigger.tab_name") == "Sheet1"
    assert convo.asking == "sheet_trigger.event"


def test_a_confirmed_event_like_tab_name_is_kept(convo: Conversation) -> None:
    open_sheet(convo)
    say_offline(convo, "change in value")
    say_offline(convo, "yes")
    assert convo.value("sheet_trigger.tab_name") == "change in value"


def test_a_plain_tab_name_is_not_questioned(convo: Conversation) -> None:
    open_sheet(convo)
    say_offline(convo, "invoice updates")
    assert convo.value("sheet_trigger.tab_name") == "invoice updates"


# --------------------------------------------------------------------------- complete save-file node
SAVE_REQUEST = "When something happens in my sheet, save it to a folder"


def test_saving_sheet_changes_asks_what_to_save_and_how(convo: Conversation) -> None:
    convo.say(SAVE_REQUEST, {"values": [v("action.channel", "save_file", "save it to a folder")]}, intent=SHEET_INTENT)
    for answer, asked_next in (
        ("Google Sheets", "sheet_trigger.file_name"),
        ("daily_invoices", "sheet_trigger.tab_name"),
        ("the Invoices tab", "sheet_trigger.event"),
        ("any cell is updated", "save_file.storage"),
        ("OneDrive", "save_file.folder"),
        ("invoice updates", "save_file.sheet_content"),
        ("the changed row", "save_file.format"),
        ("CSV", "save_file.name_pattern"),
        ("invoice_change_{date}", "dedupe.mode"),
    ):
        say_offline(convo, answer)
        assert convo.asking == asked_next, (answer, convo.reply)
    say_offline(convo, "skip the repeats")
    workflow = convo.state.workflow
    assert workflow is not None
    save = next(n for n in workflow.nodes if n.type == "save_file")
    assert save.parameters == {
        "storage": "onedrive", "folder": "invoice updates", "content": "changed_row",
        "format": "csv", "name_pattern": "invoice_change_{date}",
    }
    assert workflow.metadata.name == "Save Sheet Updates to OneDrive"


def test_saving_email_attachments_needs_no_format(convo: Conversation) -> None:
    convo.say("When an email arrives, save it to a folder",
              {"values": [v("action.channel", "save_file", "save it to a folder")]},
              intent={"goal_action": "notify", "trigger_hint": "new_email", "trigger_evidence": "an email arrives"})
    for answer in ("Gmail", "Invoices", "every one", "Dropbox", "mail archive"):
        say_offline(convo, answer)
    assert convo.asking == "save_file.email_content"
    say_offline(convo, "the attachments")
    assert convo.asking == "dedupe.mode"


# --------------------------------------------------------------------------- second demo: "save it somewhere"
SAVE_INVOICE = "Whenever a new invoice comes in, save it somewhere."


def test_a_save_request_is_not_read_as_a_notification(convo: Conversation) -> None:
    say_offline(convo, SAVE_INVOICE)
    assert convo.value("goal.action") == "backup"
    assert convo.value("action.channel") == "save_file"
    assert "notification" not in convo.reply
    assert convo.reply.startswith("Got it: you want to save new invoices.")
    assert convo.asking == "trigger.kind"


def test_saving_invoices_from_email_asks_where_and_what(convo: Conversation) -> None:
    say_offline(convo, SAVE_INVOICE)
    for answer, asked_next in (
        ("a new email", "email_trigger.provider"),
        ("gmail", "email_trigger.label"),
        ("invoices", "email_trigger.condition_mode"),
        ("on every invoice", "save_file.storage"),
        ("Google Drive", "save_file.folder"),
        ("Invoices 2026", "save_file.email_content"),
        ("the attachments", "dedupe.mode"),
    ):
        say_offline(convo, answer)
        assert convo.asking == asked_next, (answer, convo.reply)
    assert not any(key.startswith(("email.", "file_source.")) for key in convo.asked())
    say_offline(convo, "skip")
    workflow = convo.state.workflow
    assert workflow is not None
    assert [n.type for n in workflow.nodes] == ["gmail_trigger", "dedupe", "save_file"]
    assert workflow.metadata.name == "Save Invoices to Google Drive"


def test_details_about_something_is_too_vague_for_a_summary(convo: Conversation) -> None:
    from app.pipeline.validation.fields import validate
    from app.registry import FIELDS

    summary = FIELDS["summary.fields"]
    assert not validate(summary, "details about the invoice").ok
    assert not validate(summary, "info on the invoice").ok
    assert validate(summary, "invoice number, amount and due date").value == ["invoice_number", "amount", "due_date"]


def test_a_summary_per_email_trigger_asks_whether_to_batch(convo: Conversation) -> None:
    say_offline(convo, "Whenever a new invoice comes in, notify me")
    for answer in ("a new email", "gmail", "invoices", "on every invoice", "by email", "gmail", "jatin@gmail.com",
                   "summary", "invoice number, amount and due date", "Your invoices for Today"):
        say_offline(convo, answer)
    assert convo.asking == "delivery.mode"
    assert convo.reply.startswith("Send an email immediately for each invoice, or batch them")


# --------------------------------------------------------------------------- LLM client
class RateLimited(Exception):
    status_code = 429


class CountingModel:
    def __init__(self) -> None:
        self.calls = 0

    def with_structured_output(self, schema, method):
        return self

    def invoke(self, messages):
        self.calls += 1
        raise RateLimited("Rate limit reached for model")


def test_a_rate_limited_call_fails_fast_instead_of_retrying() -> None:
    model = CountingModel()
    try:
        LLMClient(model).generate(Extraction, "extractor", "hello")  # type: ignore[arg-type]
    except LLMError:
        pass
    else:
        raise AssertionError("expected LLMError")
    assert model.calls == 1
