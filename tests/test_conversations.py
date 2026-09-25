"""Scripted conversations: user turns → asked fields, stored state, generated JSON and diagram.

conftest.Conversation.say() also asserts on every turn that the generic error text
never appears (Part D, test 11) unless a test explicitly allows it.
"""

from app.pipeline.diagram.builder import build_diagram
from app.pipeline.diagram.mermaid import to_mermaid
from app.pipeline.extraction.schema import Extraction
from app.pipeline.intent.schema import IntentResult
from app.pipeline.planning import build_plan
from app.state.models import WorkflowState
from app.pipeline.validation.readiness import evaluate
from tests.conftest import Conversation, v

NOTIFY_INVOICE = {"goal_action": "notify", "entities": ["invoice", "team"], "recipient_cardinality": "multiple"}
SEND_INVOICE = {"goal_action": "send_document", "direction": "outbound_send", "entities": ["invoice", "client"],
                "recipient_cardinality": "dynamic"}
SHEET_REQUEST = "Send an email when something happens in my sheet"
SHEET_INTENT = {"goal_action": "notify", "trigger_hint": "sheet_event", "trigger_evidence": "in my sheet"}
SHEET_EXTRACTION = {"values": [v("action.channel", "email", "Send an email")]}


def assert_no_repeats(state: WorkflowState) -> None:
    """No question asked twice, and no field asked after it was filled."""
    asked = [(e.field, e.detail) for e in state.log if e.event == "asked"]
    assert len(asked) == len(set(asked)), asked
    filled: set[str] = set()
    for entry in state.log:
        if entry.event in ("filled", "overwritten", "derived"):
            filled.add(entry.field)
        if entry.event in ("reopened", "cleared"):
            filled.discard(entry.field)
        assert not (entry.event == "asked" and entry.field in filled), f"{entry.field} asked after being filled"


def assert_not_ready_unless_generated(convo: Conversation) -> None:
    ready = evaluate(convo.state, build_plan(convo.state.values(), convo.state.intent.cardinality)).ready
    assert (convo.state.workflow is not None) == ready or convo.state.mode != "collecting"


# --------------------------------------------------------------------------- 1. reference
def run_reference(convo: Conversation) -> None:
    convo.say("Whenever a new invoice arrives, notify my finance team", intent=NOTIFY_INVOICE)
    assert convo.asking == "trigger.kind"
    convo.say("Gmail")
    assert convo.value("trigger.kind") == "new_email" and convo.value("email_trigger.provider") == "gmail"
    assert convo.reply.startswith("Got it, Gmail.")
    assert convo.asking == "email_trigger.label"
    convo.say("Finance")
    assert convo.asking == "email_trigger.condition_mode"
    convo.say("Only invoices above ₹10,000", {"values": [
        v("email_trigger.condition_mode", "matching_condition", "Only invoices above"),
        v("condition.operator", "greater_than", "above"), v("condition.value", "₹10,000"),
    ]})
    assert convo.value("condition.field") == "amount"
    assert convo.asking == "action.channel"
    convo.say("Slack")
    assert convo.asking == "slack.workspace"
    convo.say("#finance in the Acme workspace", {"values": [
        v("slack.channel", "#finance"), v("slack.workspace", "Acme", "Acme workspace"),
    ]})
    assert convo.asking == "dedupe.mode"
    assert convo.state.workflow is None
    convo.say("skip duplicates")


def test_reference_conversation(convo: Conversation) -> None:
    run_reference(convo)
    workflow = convo.state.workflow
    assert convo.state.mode == "ready" and workflow is not None
    assert convo.reply.startswith("Great! I have all the required information. Generating your workflow")
    nodes = {n.type: n for n in workflow.nodes}
    assert [n.type for n in workflow.nodes] == [
        "gmail_trigger", "dedupe", "extract_fields", "condition", "slack_message", "end",
    ]
    assert nodes["gmail_trigger"].parameters["label"] == "Finance"
    assert nodes["extract_fields"].name == "Extract Invoice & Amount"
    assert nodes["condition"].parameters["numeric_value"] == 10000
    assert nodes["slack_message"].parameters["channel"] == "#finance"
    assert nodes["dedupe"].parameters["mode"] == "skip_duplicates"
    assert all("duplicate" not in str(n.parameters) for n in workflow.nodes if n.type != "dedupe")
    edges = {(e.source, e.target, e.branch) for e in workflow.edges}
    assert (nodes["condition"].id, nodes["slack_message"].id, "true") in edges
    assert (nodes["condition"].id, nodes["end"].id, "false") in edges
    assert workflow.metadata.name == "Invoice Alert via Slack" and workflow.metadata.created_at
    assert_no_repeats(convo.state)


# --------------------------------------------------------------------------- 2. excel sheet
def open_sheet(convo: Conversation) -> None:
    convo.say(SHEET_REQUEST, SHEET_EXTRACTION, intent=SHEET_INTENT)
    assert convo.reply.startswith("Got it: you want an email when something changes in your spreadsheet.")
    assert convo.asking == "sheet_trigger.platform"


def run_excel(convo: Conversation, delivery: str = "batch them into one daily summary") -> None:
    open_sheet(convo)
    convo.say("Excel")
    assert convo.asking == "sheet_trigger.file_name"
    convo.say("its is work_update")
    assert convo.value("sheet_trigger.file_name") == "work_update"
    assert convo.reply.startswith("Got it, work_update.")
    convo.say("Watch the work_update tab.")
    assert convo.value("sheet_trigger.tab_name") == "work_update"
    assert convo.asking == "sheet_trigger.event"
    convo.say("any cell is updated")
    assert convo.value("sheet_trigger.event") == "cell_updated"
    assert convo.asking == "email.provider"
    convo.say("Outlook")
    assert convo.asking == "email.recipients"
    convo.say("jatin")
    assert convo.reply.startswith('"jatin" is a name, not an email address. What\'s jatin\'s email address?')
    convo.say("jatin@example.com")
    assert convo.asking == "email.content_mode"
    convo.say("a short summary")
    assert convo.asking == "summary.fields"
    convo.say("it include the details about changes")
    assert convo.value("summary.fields") == ["changed_cell", "old_value", "new_value", "changed_by"]
    assert convo.asking == "email.subject"
    convo.say("Today's work update")
    assert convo.asking == "delivery.mode"
    assert "immediately for each row update" in convo.reply and "one summary per day/period" in convo.reply
    convo.say(delivery)


def test_excel_conversation(convo: Conversation) -> None:
    run_excel(convo)
    assert convo.value("delivery.mode") == "batched_digest"
    for answer, asked in (("daily", "delivery.time"), ("6 pm", "delivery.timezone"), ("IST", "dedupe.mode")):
        assert convo.asking == {"daily": "delivery.frequency", "6 pm": "delivery.time", "IST": "delivery.timezone"}[answer]
        convo.say(answer)
        assert convo.asking == asked
    convo.say("no")
    workflow = convo.state.workflow
    assert workflow is not None
    assert [n.type for n in workflow.nodes] == ["excel_trigger", "aggregate_batch", "compose_summary", "send_email"]
    assert workflow.nodes[0].parameters["tab_name"] == "work_update"
    assert workflow.nodes[1].parameters["time"] == "18:00"
    assert "sheet_trigger.condition_mode" not in convo.asked()
    assert_no_repeats(convo.state)


def test_excel_immediate_delivery_needs_no_schedule(convo: Conversation) -> None:
    run_excel(convo, delivery="immediately on each change")
    assert convo.asking == "dedupe.mode"
    convo.say("yes")
    assert [n.type for n in convo.state.workflow.nodes] == ["excel_trigger", "dedupe", "compose_summary", "send_email"]
    assert convo.state.workflow.nodes[-1].parameters["delivery"] == "immediate"


def test_digest_subject_on_a_per_change_trigger_asks_delivery_mode(convo: Conversation) -> None:
    open_sheet(convo)
    for answer in ("Excel", "file Work Updates", "tab Tasks", "any cell is updated", "Outlook",
                   "jatin@example.com", "fixed text", "The sheet was updated, please check it"):
        convo.say(answer)
    assert convo.asking == "email.subject"
    convo.say("Today's work update")
    assert convo.asking == "delivery.mode"


# --------------------------------------------------------------------------- 3. invoice sending
def run_invoice_until_content(convo: Conversation) -> None:
    convo.say("auto send invoice to client", intent=SEND_INVOICE)
    assert convo.asking == "trigger.kind"
    assert "What should trigger sending the invoice" in convo.reply
    convo.say("On the 1st of every month at 9 am IST", {"values": [
        v("trigger.kind", "schedule", "every month"), v("schedule_trigger.frequency", "monthly", "every month"),
        v("schedule_trigger.day_of_month", "1", "1st"), v("schedule_trigger.time", "9 am"),
        v("schedule_trigger.timezone", "IST"),
    ]})
    assert convo.asking == "document.source_kind"
    convo.say("They're PDFs in a Google Drive folder called Invoices", {"values": [
        v("document.source_kind", "folder_file", "Google Drive folder"), v("document.storage", "google_drive", "Google Drive"),
        v("document.folder", "Invoices", "folder called Invoices"),
    ]})
    assert convo.asking == "recipients.source"
    convo.say("The client list is in Google Sheets, file Clients, tab Active", {"values": [
        v("recipients.source", "sheet", "client list is in Google Sheets"),
        v("recipients.platform", "google_sheets", "Google Sheets"),
        v("recipients.file_name", "Clients", "file Clients"), v("recipients.tab_name", "Active", "tab Active"),
    ]})
    for answer, asked in (("the file name contains the client name", "recipients.email_column"),
                          ("Email", "action.channel"), ("by email", "email.provider"), ("Gmail", "email.content_mode")):
        assert convo.asking == {"recipients.email_column": "document.match_by", "action.channel": "recipients.email_column",
                                "email.provider": "action.channel", "email.content_mode": "email.provider"}[asked]
        convo.say(answer)
        assert convo.asking == asked


def test_invoice_sending_is_outbound(convo: Conversation) -> None:
    run_invoice_until_content(convo)
    convo.say("use a template")
    convo.say("Hi {client_name}, please find attached your invoice for {amount}.")
    assert convo.asking == "template.variables"
    convo.say("client_name from the Name column and amount from the Amount column")
    convo.say("Your invoice from Acme")
    workflow = convo.state.workflow
    assert workflow is not None
    types = [n.type for n in workflow.nodes]
    assert types == ["schedule_trigger", "read_sheet_rows", "loop", "read_file", "template_renderer", "send_email"]
    email = workflow.nodes[-1].parameters
    assert email["attachment"] == "{{document_1.file}}" and email["to"] == "{{item.Email}}"
    assert workflow.nodes[4].parameters["variables"] == {"client_name": "Name", "amount": "Amount"}
    assert not any(t.endswith("_trigger") and t != "schedule_trigger" for t in types)
    assert "email_trigger.label" not in convo.asked()
    diagram = build_diagram(workflow.model_dump(by_alias=True))
    assert diagram.loop == ("loop_1", ["document_1", "template_1", "email_1"])
    assert_no_repeats(convo.state)


def test_plural_recipients_with_one_address_is_questioned(convo: Conversation) -> None:
    convo.say("auto send invoice to client", intent=SEND_INVOICE)
    convo.say("manually")
    convo.say("an email attachment")
    assert convo.asking == "recipients.source"
    convo.say("jatin@gmail.com")
    assert convo.value("email.recipients") is None
    assert "You mentioned clients, but gave one address (jatin@gmail.com)" in convo.reply
    convo.say("only jatin@gmail.com")
    assert convo.value("email.recipients") == ["jatin@gmail.com"]
    assert convo.state.intent.cardinality == "single"


# --------------------------------------------------------------------------- 4. fixed text with a variable
def test_fixed_text_with_amount_switches_to_template(convo: Conversation) -> None:
    run_invoice_until_content(convo)
    convo.say("fixed text")
    assert convo.asking == "email.body_text"
    convo.say("Please pay the amount by Friday.")
    assert convo.asking == "email.body_text"
    assert 'Your text mentions "amount"' in convo.reply
    convo.say("fill it in from the data")
    assert convo.value("email.content_mode") == "template"
    assert convo.value("template.body") == "Please pay the {amount} by Friday."
    assert convo.asking == "template.variables"
    convo.say("the Amount column")
    assert convo.value("template.variables") == ["amount=Amount"]


# --------------------------------------------------------------------------- 5. goal restated as a value
def test_label_that_restates_the_goal_is_confirmed(convo: Conversation) -> None:
    convo.say("Whenever a new invoice arrives, notify my finance team", intent=NOTIFY_INVOICE)
    convo.say("Gmail")
    convo.say("send invoice to all clients")
    assert convo.value("email_trigger.label") is None
    assert convo.reply.startswith('Just to check: is "send invoice to all clients" the exact name of the label / folder?')
    convo.say("no")
    assert convo.asking == "email_trigger.label"
    convo.say("Finance")
    assert convo.value("email_trigger.label") == "Finance"


# --------------------------------------------------------------------------- 6. multi-fact
def test_multi_fact_message_fills_several_fields(convo: Conversation) -> None:
    open_sheet(convo)
    convo.say("Excel, file work_update, tab Tasks, email jatin@x.com", {"values": [
        v("sheet_trigger.platform", "excel", "Excel"), v("sheet_trigger.file_name", "work_update", "file work_update"),
        v("sheet_trigger.tab_name", "Tasks", "tab Tasks"), v("email.recipients", "jatin@x.com"),
    ]})
    assert convo.value("sheet_trigger.platform") == "excel"
    assert convo.value("sheet_trigger.file_name") == "work_update"
    assert convo.value("sheet_trigger.tab_name") == "Tasks"
    assert convo.value("email.recipients") == ["jatin@x.com"]
    assert convo.asking == "sheet_trigger.event"
    assert convo.reply.startswith("Got it: Excel / OneDrive, work_update, Tasks, jatin@x.com.")


# --------------------------------------------------------------------------- 7. restatement
def test_restating_the_request_asks_nothing_new(convo: Conversation) -> None:
    open_sheet(convo)
    convo.say("Excel")
    convo.say("its is work_update")
    filled, asking = set(convo.state.values()), convo.asking
    convo.say(SHEET_REQUEST)
    assert convo.asking == asking
    assert set(convo.state.values()) == filled
    assert not convo.state.conflicts
    assert convo.reply.startswith("I still need this before I can continue.")


# --------------------------------------------------------------------------- 8. post-generation
def test_post_generation_question_is_answered(convo: Conversation) -> None:
    run_reference(convo)
    convo.say("Which Slack channel does it post to?", followup={"kind": "question", "answer": "It posts to #finance."})
    assert convo.state.mode == "post_generation" and convo.reply == "It posts to #finance."


def test_post_generation_question_without_llm_answers_from_state(convo: Conversation) -> None:
    run_reference(convo)
    convo.say("Where does that alert go?")
    assert "#finance" in convo.reply and "is ready" not in convo.reply


def test_post_generation_edit_regenerates_json_and_diagram(convo: Conversation) -> None:
    run_excel(convo, delivery="immediately")
    convo.say("yes")
    convo.say("change recipient to a@b.com", extraction={"values": [v("email.recipients", "a@b.com")]},
              followup={"kind": "edit", "fields": ["email.recipients"]})
    assert convo.state.mode == "ready"
    email = next(n for n in convo.state.workflow.nodes if n.type == "send_email")
    assert email.parameters["to"] == ["a@b.com"]
    assert "To: a@b.com" in to_mermaid(build_diagram(convo.state.workflow.model_dump(by_alias=True)))


def test_post_generation_edit_works_without_llm(convo: Conversation) -> None:
    run_excel(convo, delivery="immediately")
    convo.say("yes")
    convo.say("change recipient to a@b.com")
    assert next(n for n in convo.state.workflow.nodes if n.type == "send_email").parameters["to"] == ["a@b.com"]


def test_post_generation_new_request_starts_fresh(convo: Conversation) -> None:
    run_reference(convo)
    request = "Every evening send me a summary"
    convo.say(request, followup={"kind": "new_request"})
    assert convo.state.pending_new_request == request and convo.state.workflow is not None
    convo.llm.scripts[IntentResult][request] = SUMMARY_INTENT
    convo.llm.scripts[Extraction][request] = SUMMARY_EXTRACTION
    convo.say("yes")
    assert convo.state.workflow is None
    assert "slack.channel" not in convo.state.fields and "email_trigger.label" not in convo.state.fields
    assert convo.asking == "schedule_trigger.time"


# --------------------------------------------------------------------------- 9. scheduled summary
SUMMARY_INTENT = {"goal_action": "report", "entities": ["summary"], "recipient_cardinality": "single",
                  "trigger_hint": "schedule", "trigger_evidence": "Every evening"}
SUMMARY_EXTRACTION = {"values": [
    v("schedule_trigger.frequency", "daily", "Every evening"), v("schedule_trigger.time", "evening"),
    v("email.content_mode", "summary", "summary"),
]}


def test_evening_summary_asks_time_timezone_source_destination(convo: Conversation) -> None:
    convo.say("Every evening send me a summary", SUMMARY_EXTRACTION, intent=SUMMARY_INTENT)
    assert convo.asking == "schedule_trigger.time"
    assert '"evening" is not an exact time' in convo.reply and "6:00 PM" in convo.reply
    convo.say("6 pm")
    assert convo.asking == "schedule_trigger.timezone"
    convo.say("IST")
    assert convo.asking == "summary.source"
    convo.say("the Daily Tasks sheet")
    assert convo.asking == "action.channel"
    for answer in ("email", "Gmail", "me@company.com", "completed tasks and blockers", "Daily work summary"):
        convo.say(answer)
    workflow = convo.state.workflow
    assert workflow is not None
    assert [n.type for n in workflow.nodes] == ["schedule_trigger", "compose_summary", "send_email"]
    assert workflow.nodes[0].parameters == {"frequency": "daily", "time": "18:00", "timezone": "Asia/Kolkata"}
    assert "delivery.mode" not in convo.asked()
    assert_no_repeats(convo.state)


# --------------------------------------------------------------------------- 11 / 12. errors and readiness
def test_unknown_goal_becomes_a_question_not_an_error(convo: Conversation) -> None:
    convo.say("hello there")
    assert convo.asking == "goal.action"
    assert convo.reply.startswith("What should this automation do")


def test_first_message_without_llm_still_gets_a_question(convo: Conversation) -> None:
    convo.llm.failing.add("auto send invoice to client")
    convo.say("auto send invoice to client")
    assert convo.value("goal.action") == "send_document"
    assert convo.asking == "trigger.kind"


def test_generic_error_only_when_llm_fails_and_nothing_is_understood(convo: Conversation) -> None:
    run_reference_until_label(convo)
    message = "hmm, let me think about how that would work for our finance people"
    convo.llm.failing.add(message)
    convo.say(message, allow_error=True)
    assert convo.reply.startswith("Sorry, I had trouble reading that message.")


def run_reference_until_label(convo: Conversation) -> None:
    convo.say("Whenever a new invoice arrives, notify my finance team", intent=NOTIFY_INVOICE)
    convo.say("Gmail")


def test_yes_to_an_either_or_question_reasks_with_options(convo: Conversation) -> None:
    run_reference_until_label(convo)
    convo.say("Finance")
    convo.say("yes")
    assert convo.asking == "email_trigger.condition_mode"
    assert convo.reply.startswith('"yes" does not tell me which one you mean. Could you pick one: every one or only when')


def test_readiness_never_triggers_early(convo: Conversation) -> None:
    run_reference(convo)
    assert convo.state.workflow is not None
    state = convo.state.model_copy(deep=True)
    state.fields["slack.channel"].status = "ambiguous"
    state.fields["slack.channel"].value = None
    state.workflow, state.mode = None, "collecting"
    assert not evaluate(state, build_plan(state.values(), state.intent.cardinality)).ready
    convo.state = state
    convo.say("thanks")
    assert convo.state.workflow is None and convo.asking == "slack.channel"


def test_generation_happens_exactly_when_ready(convo: Conversation) -> None:
    turns = [
        ("Whenever a new invoice arrives, notify my finance team", None, NOTIFY_INVOICE), ("Gmail", None, None),
        ("Finance", None, None), ("every one", None, None), ("Slack", None, None),
        ("#finance in the Acme workspace", {"values": [v("slack.channel", "#finance"), v("slack.workspace", "Acme", "Acme workspace")]}, None),
        ("no", None, None),
    ]
    for message, extraction, intent in turns:
        convo.say(message, extraction, intent=intent)
        assert_not_ready_unless_generated(convo)
    assert convo.state.workflow is not None
