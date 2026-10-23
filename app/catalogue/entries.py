"""The app catalogue: every app operation Flowwright knows, declared once.

Each entry says what the user configures (settings), what the step produces for later steps (output_fields),
whether the built-in runner can execute it, and how it is written as an n8n node. The n8n node types, versions
and parameter shapes were checked against the n8n source at N8N_VERSION (see app/catalogue/n8n.py).
"""

from app.catalogue.models import CatalogueEntry, N8nNode, OutputField, Setting, Settings
from app.catalogue.n8n import SCHEDULE_DAYS, cron_expression, if_condition, if_conditions, resource_locator
from app.pipeline.generation.schema import migrate_condition_parameters
from app.pipeline.normalize import OPERATOR_SYMBOLS

HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
COMPARISONS = tuple(OPERATOR_SYMBOLS)  # greater_than, equals, contains, is_empty, … (all ten)


def _items(value) -> list[str]:
    """A list setting as items, whether stored as a list or as comma-separated text."""
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


# -- Triggers ----------------------------------------------------------------------------------------------
MANUAL_TRIGGER = CatalogueEntry(
    key="manual.trigger", app="Manual", operation="On manual run", kind="trigger",
    description="Starts when you run the workflow yourself; its data is the input you give the run.",
    dynamic_output=True,  # the run's input JSON; n8n's manual trigger has no such data, so dynamic_path is None
    executable=True,
    n8n=N8nNode(type="n8n-nodes-base.manualTrigger", type_version=1, build_params=lambda settings: {}),
    aliases=("manual", "on click", "when clicking execute workflow", "run manually"),
)


def _webhook_params(settings: Settings) -> dict:
    # An empty path is filled with the node's webhookId by the exporter, as the n8n editor does.
    return {"httpMethod": settings.get("http_method") or "POST", "path": settings.get("path") or "", "options": {}}


WEBHOOK_TRIGGER = CatalogueEntry(
    key="webhook.trigger", app="Webhook", operation="On webhook call", kind="trigger",
    description="Starts when another system calls the workflow's URL; its data is the request body.",
    settings=(
        Setting("path", "text", "URL path", "The end of the webhook URL, e.g. new-order", required=False,
                aliases=("url_path", "webhook_path")),
        Setting("http_method", "choice", "HTTP method", "Which request method the webhook accepts",
                required=False, choices=HTTP_METHODS, aliases=("method",)),
    ),
    dynamic_output=True,
    executable=True,
    n8n=N8nNode(type="n8n-nodes-base.webhook", type_version=2.2, build_params=_webhook_params,
                dynamic_path="body.{field}"),  # n8n's webhook output is {headers, params, query, body}
    aliases=("webhook", "on call", "on webhook", "when called", "new request"),
)


def _schedule_params(settings: Settings) -> dict:
    expression = cron_expression(str(settings["time"]), settings.get("days"))
    return {"rule": {"interval": [{"field": "cronExpression", "expression": expression}]}}


SCHEDULE_TRIGGER = CatalogueEntry(
    key="schedule.trigger", app="Schedule", operation="On a schedule", kind="trigger",
    description="Starts at a set time on chosen days. The timezone becomes the workflow's timezone.",
    settings=(
        Setting("time", "time", "Time", "When it runs, e.g. 6:00 PM"),
        Setting("days", "choice", "Days", "Which days it runs", required=False, choices=SCHEDULE_DAYS,
                aliases=("day", "frequency")),
        Setting("timezone", "timezone", "Timezone", "The time's timezone, e.g. Asia/Kolkata",
                aliases=("tz", "time_zone")),
    ),
    output_fields=(
        OutputField("timestamp", "When it ran (ISO 8601)", "timestamp"),
        OutputField("date", "When it ran, readable", "Readable date"),
        OutputField("day_of_week", "The weekday it ran on", "Day of week"),
        OutputField("timezone", "The timezone it ran in", "Timezone"),
    ),
    n8n=N8nNode(type="n8n-nodes-base.scheduleTrigger", type_version=1.4, build_params=_schedule_params),
    aliases=("schedule", "every day", "every weekday", "every week", "daily", "weekly", "at a time"),
)


def _gmail_trigger_params(settings: Settings) -> dict:
    filters = {name: settings[key] for name, key in (("sender", "sender"), ("q", "search")) if settings.get(key)}
    return {"pollTimes": {"item": [{"mode": "everyMinute"}]}, "simple": True, "filters": filters, "options": {}}


GMAIL_NEW_EMAIL = CatalogueEntry(
    key="gmail.new_email", app="Gmail", operation="On new email", kind="trigger",
    description="Starts when an email arrives in Gmail (checked every minute).",
    settings=(
        Setting("sender", "email", "From", "Only emails from this address", required=False,
                aliases=("from", "from_address", "sender_email")),
        Setting("search", "text", "Search", "Only emails matching this Gmail search, e.g. subject:invoice",
                required=False, aliases=("query", "q", "search_query")),
    ),
    output_fields=(  # n8n's simplified output: message metadata, with the headers under their own names
        OutputField("id", "The message id", "id"),
        OutputField("thread_id", "The conversation id", "threadId"),
        OutputField("from", "The sender", "From"),
        OutputField("to", "The recipients", "To"),
        OutputField("subject", "The subject line", "Subject"),
        OutputField("snippet", "The start of the message text", "snippet"),
    ),
    n8n=N8nNode(type="n8n-nodes-base.gmailTrigger", type_version=1.4, build_params=_gmail_trigger_params),
    aliases=("new email", "email received", "message received", "on new message", "new message"),
)

# -- Actions -----------------------------------------------------------------------------------------------


def _gmail_send_params(settings: Settings) -> dict:
    return {"resource": "message", "operation": "send", "sendTo": ", ".join(_items(settings["to"])),
            "subject": settings["subject"], "emailType": "text", "message": settings["message"], "options": {}}


GMAIL_SEND_EMAIL = CatalogueEntry(
    key="gmail.send_email", app="Gmail", operation="Send email", kind="action",
    description="Sends an email from your Gmail account.",
    settings=(
        Setting("to", "email_list", "To", "Who receives it", aliases=("recipient", "recipients", "send_to", "to_email")),
        Setting("subject", "text", "Subject", "The subject line"),
        Setting("message", "long_text", "Message", "The email text", aliases=("body", "text", "content")),
    ),
    output_fields=(
        OutputField("id", "The sent message's id", "id"),
        OutputField("thread_id", "The conversation id", "threadId"),
    ),
    n8n=N8nNode(type="n8n-nodes-base.gmail", type_version=2.2, build_params=_gmail_send_params),
    aliases=("send an email", "send mail", "email"),
)


def _slack_params(settings: Settings) -> dict:
    channel = str(settings["channel"]).strip()
    by_id = channel[:1] in ("C", "G", "D") and channel[1:].isalnum() and channel.isupper()
    return {"resource": "message", "operation": "post", "select": "channel",
            "channelId": resource_locator(channel, "id" if by_id else "name"),
            "messageType": "text", "text": settings["message"], "otherOptions": {}}


SLACK_SEND_MESSAGE = CatalogueEntry(
    key="slack.send_message", app="Slack", operation="Send message", kind="action",
    description="Posts a message to a Slack channel.",
    settings=(
        Setting("channel", "channel", "Channel", "Where to post, e.g. #sales"),
        Setting("message", "long_text", "Message", "The message text", aliases=("text", "content", "body")),
    ),
    output_fields=(  # Slack's chat.postMessage response
        OutputField("channel", "The channel it was posted in", "channel"),
        OutputField("ts", "The message's timestamp id", "ts"),
    ),
    executable=True,  # through the incoming webhook in SLACK_WEBHOOK_URL
    n8n=N8nNode(type="n8n-nodes-base.slack", type_version=2.7, build_params=_slack_params),
    aliases=("post message", "send a message", "post", "notify", "send notification"),
)


def _sheets_row(settings: Settings) -> dict[str, str]:
    """"Name={{trigger.name}}, Total=500" → {"Name": "{{trigger.name}}", "Total": "500"}."""
    row = {}
    for item in _items(settings["row"]):
        column, _, value = item.partition("=")
        row[column.strip()] = value.strip()
    return row


def _sheets_params(settings: Settings) -> dict:
    spreadsheet = str(settings["spreadsheet"]).strip()
    row = _sheets_row(settings)
    schema = [{"id": column, "displayName": column, "required": False, "defaultMatch": False, "display": True,
               "type": "string", "canBeUsedToMatch": True} for column in row]
    return {
        "resource": "sheet", "operation": "append",
        "documentId": resource_locator(spreadsheet, "url" if spreadsheet.startswith("http") else "id"),
        "sheetName": resource_locator(str(settings["sheet"]).strip(), "name"),
        "columns": {"mappingMode": "defineBelow", "value": row, "schema": schema},
        "options": {},
    }


GOOGLE_SHEETS_APPEND_ROW = CatalogueEntry(
    key="google_sheets.append_row", app="Google Sheets", operation="Append row", kind="action",
    description="Adds a row at the end of a sheet.",
    settings=(
        Setting("spreadsheet", "text", "Spreadsheet", "The spreadsheet's URL or ID",
                aliases=("spreadsheet_id", "spreadsheet_url", "document_id", "spreadsheet_name")),
        Setting("sheet", "text", "Sheet", "The sheet (tab) name, e.g. Sheet1",
                aliases=("sheet_name", "tab", "worksheet")),
        Setting("row", "list", "Row", "Column=value pairs, e.g. Name={{trigger.name}}, Total={{trigger.total}}",
                aliases=("row_values", "values", "columns")),
    ),
    dynamic_output=True,  # the appended row, by column name
    n8n=N8nNode(type="n8n-nodes-base.googleSheets", type_version=4.7, build_params=_sheets_params,
                dynamic_path="{field}"),
    aliases=("add row", "append rows", "add a row", "insert row", "add to sheet"),
)

# -- Logic -------------------------------------------------------------------------------------------------


def _if_params(settings: Settings) -> dict:
    """Every comparison of the If node (field / operator / value, or a "conditions" list), joined by its
    combinator."""
    group = migrate_condition_parameters(dict(settings))
    conditions = [if_condition(str(c["field"]), str(c["operator"]), c.get("value")) for c in group["conditions"]]
    return {"conditions": if_conditions(conditions, group.get("combinator") or "and"), "options": {}}


IF_CONDITION = CatalogueEntry(
    key="if.condition", app="If", operation="Check condition", kind="logic",
    description="Compares a field of earlier data with a value and continues on the yes or the no branch.",
    settings=(
        Setting("field", "field", "Field to check", "The data compared, e.g. invoice.amount"),
        Setting("operator", "operator", "Comparison", "How it is compared", choices=COMPARISONS),
        Setting("value", "text", "Value to compare with",
                "Not needed for is_empty and is_not_empty", required=False),
        Setting("combinator", "choice", "Combine conditions",
                "With several comparisons: and = every one must hold, or = one is enough", required=False,
                choices=("and", "or")),
    ),
    executable=True,  # passes its input on unchanged; it produces no fields of its own
    n8n=N8nNode(type="n8n-nodes-base.if", type_version=2.4, build_params=_if_params),
    aliases=("if", "condition", "check", "filter"),
)


def _http_params(settings: Settings) -> dict:
    body = settings.get("body")
    params = {"method": settings.get("method") or ("POST" if body else "GET"), "url": settings["url"], "options": {}}
    if body:
        params |= {"sendBody": True, "contentType": "json", "specifyBody": "json", "jsonBody": body}
    return params


HTTP_REQUEST = CatalogueEntry(
    key="http_request.request", app="HTTP Request", operation="Request", kind="action",
    description="Calls any web API: the escape hatch for apps that are not in the catalogue.",
    settings=(
        Setting("url", "url", "URL", "The address to call", aliases=("endpoint",)),
        Setting("method", "choice", "Method", "GET, or POST when there is a body", required=False,
                choices=HTTP_METHODS, aliases=("http_method",)),
        Setting("body", "long_text", "Body", "The JSON to send", required=False,
                aliases=("payload", "data", "json")),
    ),
    dynamic_output=True,  # the response JSON (the runner returns it under "body", with the status code)
    executable=True,
    n8n=N8nNode(type="n8n-nodes-base.httpRequest", type_version=4.5, build_params=_http_params,
                dynamic_path="{field}"),
    aliases=("http request", "call url", "call api", "send request", "get", "post", "put", "patch",
             "delete"),
)

ENTRIES: tuple[CatalogueEntry, ...] = (
    MANUAL_TRIGGER, WEBHOOK_TRIGGER, SCHEDULE_TRIGGER, GMAIL_NEW_EMAIL,
    GMAIL_SEND_EMAIL, SLACK_SEND_MESSAGE, GOOGLE_SHEETS_APPEND_ROW, HTTP_REQUEST,
    IF_CONDITION,
)
