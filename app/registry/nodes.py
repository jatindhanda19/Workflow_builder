from dataclasses import replace

from app.registry.model import Choice, FieldDef, NodeType, Phase, When

VAGUE_PEOPLE = frozenset({
    "team", "the team", "my team", "our team", "everyone", "everybody", "people", "them", "someone",
    "anyone", "stakeholders", "me", "us", "myself", "client", "clients", "the client", "customers",
})
VAGUE_PLACES = frozenset({
    "it", "that", "there", "the sheet", "my sheet", "sheet", "a sheet", "the file", "my file", "file",
    "the folder", "folder", "inbox", "my inbox", "the inbox", "somewhere", "the tab", "drive", "my drive",
})
VAGUE_TEXT = frozenset({
    "something", "stuff", "anything", "whatever", "idk", "not sure", "a message", "message", "text",
    "details", "info", "data", "the data", "the details", "everything",
})
YES = ("yes", "y", "yeah", "yep", "sure", "please do", "ok", "okay", "correct", "right")
NO = ("no", "n", "nope", "nah", "don't", "do not")

SHEET_PLATFORMS = (
    Choice("google_sheets", "Google Sheets", ("google sheets", "google sheet", "gsheets", "google spreadsheet"), echo=True),
    Choice("excel", "Excel / OneDrive", ("excel", "microsoft excel", "excel online", "onedrive excel", "sharepoint"), echo=True),
    Choice("airtable", "Airtable", ("airtable",), echo=True),
)
MAIL_PROVIDERS = (
    Choice("gmail", "Gmail", ("gmail", "google mail"), echo=True),
    Choice("outlook", "Outlook", ("outlook", "office 365", "microsoft 365", "hotmail"), echo=True),
)
STORAGES = (
    Choice("google_drive", "Google Drive", ("google drive", "drive", "gdrive", "my drive"), echo=True),
    Choice("onedrive", "OneDrive", ("onedrive", "one drive"), echo=True),
    Choice("dropbox", "Dropbox", ("dropbox",), echo=True),
)
TIMEZONE_QUESTION = "Which timezone is that time in (for example IST / Asia/Kolkata, or UTC)?"
FREQUENCIES = (
    Choice("daily", "every day", ("daily", "every day", "each day", "every evening", "every morning", "every night", "nightly"), echo=True),
    Choice("weekdays", "every weekday", ("weekdays", "every weekday", "monday to friday", "working days"), echo=True),
    Choice("weekly", "once a week", ("weekly", "every week", "once a week", "every monday", "every friday"), echo=True),
    Choice("monthly", "once a month", ("monthly", "every month", "once a month"), echo=True),
)
WEEKDAYS = tuple(
    Choice(day, day.capitalize(), (day, day[:3], f"every {day}"), echo=True)
    for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
)


def f(name: str, label: str, description: str, question: str, phase: Phase, **kw) -> FieldDef:
    return FieldDef(name, label, description, question, phase, **kw)


def node(node_id: str, label: str, category: str, stage: int, *fields: FieldDef, **kw) -> NodeType:
    bound = tuple(replace(item, node=node_id) for item in fields)
    return NodeType(node_id, label, category, stage, bound, **kw)  # type: ignore[arg-type]


def schedule_fields(phase: Phase, frequency_key: str, when: tuple[When, ...] = ()) -> tuple[FieldDef, ...]:
    return (
        f("frequency", "Frequency", "How often it runs", "How often should it run: {options}?", phase,
          kind="choice", choices=FREQUENCIES, applies_when=when),
        f("day_of_week", "Day of week", "Weekday for a weekly schedule", "On which day of the week?", phase,
          kind="choice", choices=WEEKDAYS, applies_when=(*when, When(frequency_key, ("weekly",))),
          depends_on=(frequency_key,)),
        f("day_of_month", "Day of month", "Day number for a monthly schedule",
          "On which day of the month (1-31, or 'last')?", phase, kind="day_of_month",
          applies_when=(*when, When(frequency_key, ("monthly",))), depends_on=(frequency_key,)),
        f("time", "Time", "Exact time of day", "At what exact time (for example 6:00 PM)?", phase,
          kind="time", applies_when=when),
        f("timezone", "Timezone", "Timezone of the schedule", TIMEZONE_QUESTION, phase,
          kind="timezone", applies_when=when),
    )


# --------------------------------------------------------------------------- triggers
SCHEDULE_TRIGGER = node(
    "schedule_trigger", "Schedule trigger", "trigger", 0,
    *schedule_fields(Phase.EVENT, "schedule_trigger.frequency"),
    selector=("trigger.kind", Choice("schedule", "a schedule", (
        "schedule", "on a schedule", "scheduled", "every day", "daily", "weekly", "monthly", "every week",
        "every month", "every morning", "every evening", "every monday", "cron", "timer",
    ))),
    workflow_type="schedule_trigger", display_name="Schedule Trigger",
    summary=("{frequency} at {time} ({timezone})", "{frequency}"),
)
SHEET_TRIGGER = node(
    "sheet_trigger", "Sheet trigger", "trigger", 0,
    f("platform", "Spreadsheet app", "Which spreadsheet app holds the sheet",
      "Which spreadsheet app is it: {options}?", Phase.TRIGGER, kind="choice", choices=SHEET_PLATFORMS),
    f("file_name", "Spreadsheet file", "Name of the spreadsheet file (not a tab)",
      "What is the name of the spreadsheet file (the document itself, not a tab inside it)?", Phase.LOCATION,
      kind="name", vague=VAGUE_PLACES, nouns=("file name", "file", "spreadsheet", "workbook", "document")),
    f("tab_name", "Tab", "Tab (worksheet) inside the file", "Which tab inside {file_name} should I watch?",
      Phase.LOCATION, kind="name", vague=VAGUE_PLACES, nouns=("tab name", "tab", "worksheet"),
      depends_on=("sheet_trigger.file_name",)),
    f("event", "Trigger event", "Which change in the sheet starts the workflow",
      "What should start the workflow: {options}?", Phase.EVENT, kind="choice", choices=(
          Choice("row_added", "a new row is added", ("new row", "row added", "row is added", "a new row", "new entry")),
          Choice("cell_updated", "any cell is updated", ("any cell", "cell updated", "any edit", "any update", "something is edited")),
          Choice("column_value_changed", "a value in a specific column changes", (
              "value changes", "column changes", "status changes", "value changed", "column value changes")),
      )),
    f("watched_column", "Watched column", "Column whose value is watched",
      "Which column should I watch (for example the column that holds the status)?", Phase.EVENT,
      kind="name", vague=VAGUE_PLACES | frozenset({"column", "the column"}), nouns=("column",),
      applies_when=(When("sheet_trigger.event", ("column_value_changed",)),), depends_on=("sheet_trigger.event",)),
    f("condition_mode", "Trigger condition", "Any change, or only a specific value",
      "Should {change_phrase} trigger this, or only when it becomes a specific value (for example \"Done\")?",
      Phase.CONDITION, kind="choice", choices=(
          Choice("any_change", "any change", ("any change", "any", "all", "every change", "every time", "always")),
          Choice("specific_value", "only a specific value", ("specific value", "only a specific value", "only when", "only if")),
      ), applies_when=(When("sheet_trigger.event", ("column_value_changed",)),),
      depends_on=("sheet_trigger.watched_column",)),
    selector=("trigger.kind", Choice("sheet_event", "a new or changed row in a sheet", (
        "sheet", "spreadsheet", "my sheet", "a sheet", "new row", "row added", "google sheets", "excel", "airtable",
    ))),
    workflow_type="{platform}_trigger", display_name="{platform} Trigger",
    summary=("{file_name} / {tab_name}: {watched_column} changes", "{file_name} / {tab_name}: {event}", "{file_name}"),
)
EMAIL_TRIGGER = node(
    "email_trigger", "Email trigger", "trigger", 0,
    f("provider", "Mailbox", "The mailbox that receives the emails", "Which mailbox is it: {options}?",
      Phase.TRIGGER, kind="choice",
      choices=(*MAIL_PROVIDERS, Choice("imap", "another mailbox (IMAP)", ("imap", "other mailbox"), echo=True))),
    f("label", "Label / folder", "Mailbox label or folder that is watched",
      "Which {provider} label or folder should I watch for new {item}s?", Phase.LOCATION,
      kind="name", vague=VAGUE_PLACES, nouns=("label", "folder")),
    f("condition_mode", "Trigger condition", "Every email, or only emails matching a condition",
      "Should this run for every {item}, or only when a condition is met (for example an amount above a threshold)?",
      Phase.CONDITION, kind="choice", choices=(
          Choice("all_emails", "every one", ("all", "every", "every one", "all of them", "always", "every email")),
          Choice("matching_condition", "only when a condition is met", ("only some", "only when", "only if", "only certain", "conditional")),
      )),
    f("filter", "Sender / subject filter", "Optional sender or subject filter",
      "Should I only include emails from a specific sender or with a specific subject?", Phase.OPTIONAL,
      required=False),
    selector=("trigger.kind", Choice("new_email", "a new email", (
        "email", "new email", "an email", "emails", "inbox", "mailbox", "gmail", "outlook", "email arrives",
    ))),
    workflow_type="{provider}_trigger", display_name="{provider} Trigger",
    summary=("Label: {label}",),
)
FILE_TRIGGER = node(
    "file_trigger", "New-file trigger", "trigger", 0,
    f("storage", "Storage", "Where the folder lives", "Which storage is the folder in: {options}?",
      Phase.TRIGGER, kind="choice", choices=STORAGES),
    f("folder", "Folder", "The folder that is watched for new files", "Which folder should I watch?",
      Phase.LOCATION, kind="name", vague=VAGUE_PLACES, nouns=("folder",)),
    selector=("trigger.kind", Choice("new_file", "a new file in a folder", (
        "new file", "a new file", "file is added", "file uploaded", "new upload",
    ))),
    workflow_type="{storage}_trigger", display_name="{storage} Trigger", summary=("Folder: {folder}",),
)
FORM_TRIGGER = node(
    "form_trigger", "Form trigger", "trigger", 0,
    f("platform", "Form app", "The form tool", "Which form tool is it: {options}?", Phase.TRIGGER,
      kind="choice", choices=(
          Choice("google_forms", "Google Forms", ("google forms", "google form"), echo=True),
          Choice("typeform", "Typeform", ("typeform",), echo=True),
          Choice("microsoft_forms", "Microsoft Forms", ("microsoft forms", "ms forms"), echo=True),
      )),
    f("form_name", "Form", "Name of the form", "What is the name of the form?", Phase.LOCATION,
      kind="name", vague=frozenset({"form", "the form", "my form"}), nouns=("form",)),
    selector=("trigger.kind", Choice("form_submission", "a form submission", (
        "form", "form submission", "form response", "form responses", "new response", "responses", "submission",
    ))),
    workflow_type="{platform}_trigger", display_name="{platform} Trigger", summary=("Form: {form_name}",),
)
MANUAL_TRIGGER = node(
    "manual_trigger", "Manual trigger", "trigger", 0,
    selector=("trigger.kind", Choice("manual", "manually (on demand)", (
        "manual", "manually", "on demand", "button", "when i click", "myself", "by hand", "when i run it",
    ))),
    workflow_type="manual_trigger", display_name="Manual Trigger", summary=("Run on demand",),
)

# --------------------------------------------------------------------------- logic
DEDUPE = node(
    "dedupe", "Dedupe", "logic", 10,
    f("mode", "Duplicate handling", "Skip repeated events, or process every one",
      "If the same {item} shows up more than once (a duplicate or a repeated update), should I skip the repeats so it only runs once?",
      Phase.DUPLICATES, kind="choice", choices=(
          Choice("skip_duplicates", "skip duplicates", YES + ("skip", "skip duplicates", "ignore duplicates", "only once", "dedupe")),
          Choice("process_all", "process every event", NO + ("every time", "allow duplicates", "process all", "send every time")),
      )),
    selected_when=(When("fact.event_trigger", ("yes",)),),
    emit_when=(When("dedupe.mode", ("skip_duplicates",)),),
    workflow_type="dedupe", display_name="Dedupe", summary=("Skip repeated {item}s",),
)
EXTRACT = node(
    "extract", "Extract fields", "data", 20,
    selected_when=(When("fact.email_extract", ("yes",)),),
    workflow_type="extract_fields", display_name="Extract {Entity} & {Condition_field}", summary=("Fields: {fields}",),
)
CONDITION = node(
    "condition", "Condition", "logic", 30,
    f("field", "Condition field", "The value the condition checks",
      "Which value should the condition check (for example the amount, or a column)?", Phase.CONDITION,
      kind="name", vague=VAGUE_TEXT, nouns=("column", "field")),
    f("operator", "Condition operator", "How the value is compared", "How should {field} be compared: {options}?",
      Phase.CONDITION, kind="choice", choices=(
          Choice("equals", "equals", ("equals", "is", "=", "becomes", "changes to", "is set to")),
          Choice("not_equals", "is not", ("not equals", "is not", "!=")),
          Choice("greater_than", "is above", ("greater than", "above", "more than", "over", ">", "exceeds")),
          Choice("less_than", "is below", ("less than", "below", "under", "<")),
          Choice("contains", "contains", ("contains", "includes")),
      )),
    f("value", "Condition value", "The value to compare against", "What value should {field} be compared against?",
      Phase.CONDITION, vague=VAGUE_TEXT),
    selected_when=(When("fact.conditional", ("yes",)),),
    workflow_type="condition", display_name="Condition ({Field} {operator_symbol} {value})",
    summary=("{field} {operator_symbol} {value}",),
)
DELIVERY = node(
    "delivery", "Aggregate / Batch", "logic", 40,
    f("mode", "Delivery mode", "One email per change, or a batched digest",
      "Send an email immediately for each {item}, or batch them into one summary per day/period?",
      Phase.DELIVERY, kind="choice", choices=(
          Choice("immediate", "immediately on each change", (
              "immediately", "immediate", "right away", "instantly", "each change", "every change",
              "on each change", "real time", "as it happens")),
          Choice("batched_digest", "one batched summary", (
              "batch", "batched", "batch them", "batch changes", "digest", "daily digest", "one summary",
              "daily summary", "once a day", "per day", "end of day", "summary per day")),
      )),
    *schedule_fields(Phase.DELIVERY, "delivery.frequency", (When("delivery.mode", ("batched_digest",)),)),
    selected_when=(When("fact.digest", ("yes",)),),
    emit_when=(When("delivery.mode", ("batched_digest",)),),
    workflow_type="aggregate_batch", display_name="Aggregate / Batch",
    summary=("{frequency} at {time} ({timezone})",),
)
LOOP = node(
    "loop", "Loop", "logic", 60,
    selected_when=(When("fact.per_record", ("yes",)),),
    workflow_type="loop", display_name="Loop: For Each {Item}", summary=("For each {item}",),
)
DELAY = node(
    "delay", "Delay", "logic", 90,
    f("duration", "Delay", "Wait before the action runs", "How long should it wait before continuing?",
      Phase.OPTIONAL, kind="duration", required=False),
    emit_when=(When("fact.has_delay", ("yes",)),),
    workflow_type="delay", display_name="Delay", summary=("Wait {duration}",),
)

# --------------------------------------------------------------------------- data
RECIPIENTS = node(
    "recipients", "Recipient list", "data", 50,
    f("source", "Recipient source", "Where the list of recipients comes from",
      "Where does the list of {recipients} come from: {options}?", Phase.RECIPIENT_SOURCE, kind="choice", choices=(
          Choice("sheet", "a spreadsheet", ("sheet", "spreadsheet", "google sheets", "excel", "airtable", "a list in a sheet")),
          Choice("crm", "a CRM list", ("crm", "hubspot", "salesforce", "zoho")),
          Choice("trigger_data", "a column in the triggering data", ("trigger data", "from the data", "in the row", "from the row")),
          Choice("fixed_list", "fixed addresses I will type", ("fixed", "fixed addresses", "i will type them", "these addresses")),
      )),
    f("platform", "List app", "Spreadsheet app of the list", "Which spreadsheet app holds the list: {options}?",
      Phase.RECIPIENT_SOURCE, kind="choice", choices=SHEET_PLATFORMS,
      applies_when=(When("recipients.source", ("sheet",)),)),
    f("file_name", "List file", "Spreadsheet file with the list", "What is the name of the spreadsheet file with the list?",
      Phase.RECIPIENT_SOURCE, kind="name", vague=VAGUE_PLACES, nouns=("file name", "file", "spreadsheet"),
      applies_when=(When("recipients.source", ("sheet",)),)),
    f("tab_name", "List tab", "Tab with the list", "Which tab holds the list?", Phase.RECIPIENT_SOURCE,
      kind="name", vague=VAGUE_PLACES, nouns=("tab name", "tab", "worksheet"),
      applies_when=(When("recipients.source", ("sheet",)),)),
    f("email_column", "Email column", "Column that holds each email address",
      "Which column holds each {recipient}'s email address?", Phase.RECIPIENT_SOURCE, kind="name",
      nouns=("column",), applies_when=(When("recipients.source", ("sheet",)),)),
    f("crm", "CRM", "The CRM that holds the list", "Which CRM: {options}?", Phase.RECIPIENT_SOURCE,
      kind="choice", choices=(
          Choice("hubspot", "HubSpot", ("hubspot",), echo=True),
          Choice("salesforce", "Salesforce", ("salesforce",), echo=True),
          Choice("zoho", "Zoho CRM", ("zoho",), echo=True),
      ), applies_when=(When("recipients.source", ("crm",)),)),
    f("crm_list", "CRM list", "List or segment in the CRM", "Which list or segment in the CRM?",
      Phase.RECIPIENT_SOURCE, kind="name", nouns=("list", "segment"),
      applies_when=(When("recipients.source", ("crm",)),)),
    f("email_field", "Email field", "Field of the triggering data that holds the address",
      "Which field of the triggering data holds the email address?", Phase.RECIPIENT_SOURCE, kind="name",
      nouns=("column", "field"), applies_when=(When("recipients.source", ("trigger_data",)),)),
    selected_when=(When("fact.plural_recipients", ("yes",)),),
    emit_when=(When("recipients.source", ("sheet", "crm")),),
    workflow_type="read_{source}_rows", display_name="Read {Recipients}",
    summary=("{file_name} / {tab_name}", "{crm_list}"),
)
DOCUMENT = node(
    "document", "Document", "data", 70,
    f("source_kind", "Document source", "Where the document comes from",
      "Where does the {entity} come from: {options}?", Phase.SOURCE, kind="choice", choices=(
          Choice("folder_file", "a file in a folder (Drive, OneDrive, Dropbox)", ("folder", "a folder", "drive", "google drive", "onedrive", "dropbox", "file in a folder", "pdf")),
          Choice("email_attachment", "an email attachment", ("attachment", "email attachment", "attached")),
      )),
    f("storage", "Document storage", "Storage that holds the documents", "Which storage holds the files: {options}?",
      Phase.SOURCE, kind="choice", choices=STORAGES, applies_when=(When("document.source_kind", ("folder_file",)),)),
    f("folder", "Document folder", "Folder that holds the documents", "Which folder holds the {entity} files?",
      Phase.SOURCE, kind="name", vague=VAGUE_PLACES, nouns=("folder",),
      applies_when=(When("document.source_kind", ("folder_file",)),)),
    f("match_by", "Matching rule", "How each recipient's document is found",
      "How do I find each {recipient}'s {entity} (for example: the file name contains the {recipient} name)?",
      Phase.SOURCE, kind="free_text", vague=VAGUE_TEXT, applies_when=(When("fact.per_record", ("yes",)),)),
    selected_when=(When("goal.action", ("send_document",)),),
    workflow_type="read_file", display_name="Read {Entity} File", summary=("{storage}: {folder}", "{source_kind}"),
    per_item=True,
)
FILE_SOURCE = node(
    "file_source", "Files to copy", "data", 70,
    f("storage", "Source storage", "Where the files to back up are", "Which storage should I back up from: {options}?",
      Phase.SOURCE, kind="choice", choices=STORAGES),
    f("folder", "Source folder", "Folder to back up", "Which folder should I back up (or 'all files')?",
      Phase.SOURCE, kind="name", nouns=("folder",)),
    selected_when=(When("fact.copies_folder", ("yes",)),),
    workflow_type="list_files", display_name="List Files ({storage})", summary=("Folder: {folder}",),
)
SUMMARY = node(
    "summary", "Summary", "data", 80,
    f("source", "Summary source", "Where the data for the summary comes from",
      "Where should the data for the summary come from (for example a specific sheet or project tracker)?",
      Phase.SOURCE, kind="name", vague=VAGUE_TEXT | VAGUE_PLACES,
      applies_when=(When("trigger.kind", ("schedule", "manual")),)),
    f("fields", "Summary includes", "What the summary should include",
      "What should the summary include (for example which columns or details)?", Phase.CONTENT,
      kind="field_list", vague=VAGUE_TEXT),
    selected_when=(When("fact.summary", ("yes",)),),
    workflow_type="compose_summary", display_name="Compose Summary", summary=("Includes: {fields}",),
)
TEMPLATE = node(
    "template", "Template", "data", 85,
    f("body", "Template text", "Message text with {placeholders}",
      "What should the message say? Put anything that changes per message in braces, for example {{client_name}}.",
      Phase.CONTENT, kind="free_text", vague=VAGUE_TEXT),
    f("variables", "Template variables", "Which data field fills each placeholder",
      "Which data field should fill each placeholder ({placeholders})?", Phase.CONTENT, kind="mapping",
      depends_on=("template.body",)),
    selected_when=(When("email.content_mode", ("template",)),),
    workflow_type="template_renderer", display_name="Render Template", summary=("Variables: {variables}",),
    per_item=True,
)

# --------------------------------------------------------------------------- actions
EMAIL = node(
    "email", "Send email", "action", 100,
    f("provider", "Email provider", "Service that sends the email", "Which email service should send it: {options}?",
      Phase.CHANNEL, kind="choice", choices=(*MAIL_PROVIDERS, Choice("smtp", "SMTP", ("smtp", "own mail server"), echo=True))),
    f("recipients", "Recipient address(es)", "Email address(es) that receive it",
      "Which email address(es) should receive it?", Phase.RECIPIENTS, kind="email_list", vague=VAGUE_PEOPLE,
      applies_when=(When("fact.fixed_recipients", ("yes",)),)),
    f("content_mode", "Content", "Fixed text, a template with variables, or a generated summary",
      "Should the email be {content_options}?", Phase.CONTENT, kind="choice", choices=(
          Choice("fixed_text", "the same fixed text every time", ("fixed", "fixed text", "exact text", "same text", "static text")),
          Choice("template", "a template with values filled in", ("template", "a template", "personalised", "personalized", "with variables", "use a template")),
          Choice("summary", "a summary generated from the data", (
              "summary", "a summary", "short summary", "a short summary", "brief summary", "brief update",
              "a brief update", "short update", "digest")),
      )),
    f("body_text", "Email text", "Exact text of the email", "What exact text should the email contain?",
      Phase.CONTENT, kind="free_text", vague=VAGUE_TEXT,
      applies_when=(When("email.content_mode", ("fixed_text",)),)),
    f("subject", "Subject", "Email subject line", "What should the email subject line be?", Phase.SUBJECT,
      kind="free_text", vague=VAGUE_TEXT),
    selector=("action.channel", Choice("email", "by email", (
        "email", "e-mail", "mail", "by email", "send an email", "an email", "gmail", "outlook",
    ))),
    workflow_type="send_email", display_name="Send Email ({provider})",
    summary=("To: {to}",),
    per_item=True,
)
SLACK = node(
    "slack", "Slack message", "action", 100,
    f("workspace", "Slack workspace", "Slack workspace", "Which Slack workspace and channel should I post to?",
      Phase.CHANNEL, kind="name", vague=VAGUE_PLACES, nouns=("workspace",)),
    f("channel", "Slack channel", "Slack channel", "Which Slack channel in {workspace} (for example #finance)?",
      Phase.RECIPIENTS, kind="slack_channel", vague=VAGUE_PEOPLE | VAGUE_PLACES),
    f("message", "Slack message", "Text of the message", "What should the Slack message say?", Phase.CONTENT,
      kind="free_text", vague=VAGUE_TEXT, required_when=(When("goal.action", ("post_message",)),)),
    selector=("action.channel", Choice("slack", "a Slack message", ("slack", "slack message", "on slack", "post on slack"))),
    workflow_type="slack_message", display_name="Slack ({channel})", summary=("{channel}",),
)
SHEET_ROW = node(
    "sheet_row", "Write sheet row", "action", 100,
    f("platform", "Destination app", "Spreadsheet app to write to", "Which spreadsheet app should I write to: {options}?",
      Phase.RECIPIENTS, kind="choice", choices=SHEET_PLATFORMS),
    f("file_name", "Destination file", "Spreadsheet file to write to", "What is the name of the spreadsheet file to write to?",
      Phase.RECIPIENTS, kind="name", vague=VAGUE_PLACES, nouns=("file name", "file", "spreadsheet")),
    f("tab_name", "Destination tab", "Tab to write to", "Which tab should the rows go into?", Phase.RECIPIENTS,
      kind="name", vague=VAGUE_PLACES, nouns=("tab name", "tab", "worksheet")),
    f("operation", "Write mode", "Append a new row or update an existing one",
      "Should I add a new row each time, or update an existing row?", Phase.CONTENT, kind="choice", choices=(
          Choice("append_row", "add a new row", ("append", "add a new row", "new row", "add a row", "add")),
          Choice("update_row", "update an existing row", ("update", "update a row", "update existing", "overwrite")),
      )),
    f("columns", "Columns", "Which fields go into the sheet",
      "Which fields should go into the sheet (for example name, email, answer), or all fields?", Phase.CONTENT,
      kind="field_list"),
    selector=("action.channel", Choice("sheet_row", "a row in a spreadsheet", (
        "sheet", "a sheet", "to a sheet", "spreadsheet", "add to a sheet", "append", "add a row", "google sheets",
    ))),
    workflow_type="sheet_{operation}", display_name="Write to {platform}", summary=("{file_name} / {tab_name}",),
)
SAVE_FILE = node(
    "save_file", "Save file", "action", 100,
    f("storage", "Destination storage", "Where the files are saved", "Where should the files be saved: {options}?",
      Phase.RECIPIENTS, kind="choice", choices=(*STORAGES, Choice("s3", "Amazon S3", ("s3", "amazon s3", "aws"), echo=True))),
    f("folder", "Destination folder", "Folder the files are saved into", "Which folder should they be saved into?",
      Phase.RECIPIENTS, kind="name", nouns=("folder",)),
    f("sheet_content", "File content", "What each saved file contains",
      "What should be saved each time: {options}?", Phase.CONTENT, kind="choice", choices=(
          Choice("changed_row", "the changed row", ("changed row", "the changed row", "the row", "row", "changed rows", "the changes", "only the change")),
          Choice("whole_sheet", "a copy of the whole spreadsheet", ("whole sheet", "whole spreadsheet", "entire sheet", "full sheet", "whole file", "a copy", "copy of the sheet")),
      ), applies_when=(When("trigger.kind", ("sheet_event",)),)),
    f("email_content", "File content", "What is saved from each email",
      "What should be saved from each email: {options}?", Phase.CONTENT, kind="choice", choices=(
          Choice("attachments", "its attachments", ("attachments", "the attachments", "attachment", "attached files", "its attachments")),
          Choice("email_message", "the email itself", ("the email", "email itself", "the email itself", "the message", "whole email", "email body")),
      ), applies_when=(When("trigger.kind", ("new_email",)),)),
    f("format", "File format", "Format of each saved file", "Which file format should I save: {options}?",
      Phase.CONTENT, kind="choice", choices=(
          Choice("csv", "CSV", ("csv", "comma separated"), echo=True),
          Choice("xlsx", "Excel (.xlsx)", ("xlsx", "excel", "excel file", "spreadsheet"), echo=True),
          Choice("pdf", "PDF", ("pdf",), echo=True),
          Choice("json", "JSON", ("json",), echo=True),
      ), applies_when=(When("fact.generated_file", ("yes",)),), depends_on=("save_file.email_content",)),
    f("name_pattern", "File name", "Name of each saved file",
      "What should each file be named? Add {{date}} or {{time}} to keep names unique (for example invoices_{{date}}).",
      Phase.CONTENT, kind="free_text", vague=VAGUE_TEXT, applies_when=(When("fact.generated_file", ("yes",)),),
      depends_on=("save_file.format",)),
    selector=("action.channel", Choice("save_file", "save files to another folder", (
        "save", "save them", "store", "copy files", "save to", "backup to", "back up to",
    ))),
    workflow_type="save_file", display_name="Save File ({storage})", summary=("Folder: {folder}",),
)

# --------------------------------------------------------------------------- setup (planning questions)
GOAL_CHOICES = (
    Choice("notify", "notify you when something happens", ("notify", "notification", "alert", "let me know", "tell me", "notify me")),
    Choice("send_document", "send a document (invoice, report) to someone", ("send a document", "send invoice", "send invoices", "send the invoice", "send a file")),
    Choice("report", "send a scheduled report or summary", ("report", "summary", "scheduled report", "a summary")),
    Choice("sync_data", "copy data between apps", ("sync", "copy data", "copy", "add to a sheet", "save responses")),
    Choice("backup", "back up files", ("backup", "back up", "archive"), implies=(("action.channel", "save_file"),)),
    Choice("post_message", "post a message", ("post a message", "post", "announce")),
)

# Every node that can appear in a workflow. Adding a node type = adding it here.
WORKFLOW_NODES: tuple[NodeType, ...] = (
    SCHEDULE_TRIGGER, SHEET_TRIGGER, EMAIL_TRIGGER, FILE_TRIGGER, FORM_TRIGGER, MANUAL_TRIGGER,
    DEDUPE, EXTRACT, CONDITION, DELIVERY, RECIPIENTS, LOOP, DOCUMENT, FILE_SOURCE, SUMMARY, TEMPLATE, DELAY,
    EMAIL, SLACK, SHEET_ROW, SAVE_FILE,
)


def _selector_choices(key: str) -> tuple[Choice, ...]:
    return tuple(n.selector[1] for n in WORKFLOW_NODES if n.selector and n.selector[0] == key)


GOAL = node(
    "goal", "Goal", "setup", -30,
    f("action", "Goal", "What the automation should achieve", "What should this automation do: {options}?",
      Phase.GOAL, kind="choice", choices=GOAL_CHOICES),
)
TRIGGER = node(
    "trigger", "Trigger", "setup", -20,
    f("kind", "Trigger", "What starts the workflow", "What should trigger {goal_phrase}: {options}?",
      Phase.TRIGGER, kind="choice", choices=_selector_choices("trigger.kind")),
)
ACTION = node(
    "action", "Action", "setup", -10,
    f("channel", "Action channel", "Where the result goes", "{channel_question}: {options}?",
      Phase.CHANNEL, kind="choice", choices=_selector_choices("action.channel")),
)

NODE_TYPES: tuple[NodeType, ...] = (GOAL, TRIGGER, ACTION, *WORKFLOW_NODES)
