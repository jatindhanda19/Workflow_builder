import re

from app.pipeline.intent.schema import IntentResult

GOAL_RULES: tuple[tuple[str, str], ...] = (
    # "save it somewhere" stores files; saving *into a sheet* is sync_data below.
    ("backup", r"\bback ?up\b|\barchive\b|\b(?:save|store)\b(?!.*\b(?:sheet|spreadsheet|table|database|crm)\b)"),
    ("post_message", r"\bpost\b.*\bmessage\b|\bpost (?:a|an|the)\b|\bannounce\b"),
    ("sync_data", r"\b(?:add|append|copy|sync|save|log)\b.*\b(?:to|into)\b.*\b(?:sheet|spreadsheet|table|database|crm)\b"),
    ("send_document", r"\bsend\w*\b.*\b(?:invoice|document|pdf|statement|receipt|quote|contract)s?\b"),
    ("report", r"\b(?:summary|report|digest)\b(?!.*\bwhen\b)"),
    ("notify", r"\b(?:notify|alert|let me know|tell me|remind)\b|\bwhen(?:ever)?\b"),
)
DIRECTIONS = {
    "notify": "inbound_event_alert", "send_document": "outbound_send", "post_message": "outbound_send",
    "report": "scheduled_report", "sync_data": "data_sync", "backup": "data_sync",
}
TRIGGER_RULES: tuple[tuple[str, str], ...] = (
    ("form_submission", r"\bform (?:responses?|submissions?)\b|\bnew (?:form )?responses?\b"),
    ("new_email", r"\bnew emails?\b|\bemails? arrives?\b|\binbox\b"),
    ("new_file", r"\bnew files?\b|\bfiles? (?:is |are )?(?:added|uploaded)\b"),
    ("sheet_event", r"\b(?:in|on) (?:my|the|a|our) (?:google )?(?:sheet|spreadsheet)\b|\bsheet changes\b"),
    ("schedule", r"\bevery (?:day|week|month|morning|evening|night|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b|\b(?:daily|weekly|monthly|nightly)\b"),
)
# Outbound only: "when an email arrives" is a trigger, not the channel.
CHANNEL_RULES: tuple[tuple[str, str], ...] = (
    ("email", r"\b(?:send|sends|sending|shoot|drop)\s+(?:me\s+|us\s+|them\s+)?(?:an?\s+)?e-?mails?\b(?!\s+arrives)"
              r"|\be-?mail\s+(?:me|us|them|the team|my team)\b|\b(?:by|via|through|over)\s+e-?mail\b"),
    ("slack", r"\bslack\b"),
)
ENTITIES = (
    "invoice", "client", "customer", "report", "summary", "row", "file", "message", "response", "form",
    "team", "lead", "order", "receipt", "payment", "ticket", "document",
)
RECORD_PEOPLE = ("client", "customer", "lead", "subscriber", "vendor", "supplier")

def classify_by_rules(message: str) -> IntentResult:
    text = message.lower()
    goal, goal_evidence = _first_match(GOAL_RULES, text)
    trigger, trigger_evidence = _first_match(TRIGGER_RULES, text)
    channel, channel_evidence = _first_match(CHANNEL_RULES, text)
    return IntentResult(
        goal_action=goal,  # type: ignore[arg-type]
        goal_evidence=goal_evidence,
        direction=DIRECTIONS.get(goal or ""),  # type: ignore[arg-type]
        entities=[e for e in ENTITIES if re.search(rf"\b{e}s?\b", text)],
        recipient_cardinality=_cardinality(text),  # type: ignore[arg-type]
        trigger_hint=trigger,  # type: ignore[arg-type]
        trigger_evidence=trigger_evidence,
        channel_hint=channel,  # type: ignore[arg-type]
        channel_evidence=channel_evidence,
    )

def fill_gaps(primary: IntentResult, fallback: IntentResult) -> IntentResult:
    """Keep everything the LLM said; take only missing parts from the rules."""
    update = {
        name: getattr(fallback, name)
        for name in IntentResult.model_fields
        if getattr(primary, name) in (None, []) and getattr(fallback, name) not in (None, [])
    }
    return primary.model_copy(update=update)

def direction_for(goal: str | None) -> str | None:
    return DIRECTIONS.get(goal or "")

def _first_match(rules: tuple[tuple[str, str], ...], text: str) -> tuple[str | None, str | None]:
    for value, pattern in rules:
        match = re.search(pattern, text)
        if match:
            return value, match.group(0)
    return None, None

def _cardinality(text: str) -> str | None:
    if re.search(r"\b(?:each|every|all)\s+(?:of\s+(?:my|our|the)\s+)?(?:" + "|".join(RECORD_PEOPLE) + r")s?\b", text):
        return "dynamic"
    if re.search(r"\b(?:" + "|".join(RECORD_PEOPLE) + r")s?\b", text):
        return "dynamic"
    if re.search(r"\b(?:team|everyone|managers|staff)\b", text):
        return "multiple"
    if re.search(r"\b(?:me|my manager|my boss)\b", text):
        return "single"
    return None
