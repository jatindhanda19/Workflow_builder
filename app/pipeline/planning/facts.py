"""Derived facts the registry's conditions can refer to (fact.*).

Facts summarise what the answers so far imply, e.g. "the trigger fires per change"
or "recipients come from a list", so registry entries can stay declarative.
"""

from app.state.models import FieldValue

EVENT_TRIGGERS = ("new_email", "sheet_event", "new_file", "form_submission")
LIST_SOURCES = ("sheet", "crm")
DIGEST_WORDS = ("today", "today's", "daily", "digest", "summary", "end of day", "this week", "weekly")
YES = "yes"


def derive_facts(values: dict[str, FieldValue], cardinality: str | None) -> dict[str, str]:
    kind = values.get("trigger.kind")
    channel = values.get("action.channel")
    flags = {
        "event_trigger": kind in EVENT_TRIGGERS,
        "change_trigger": kind == "sheet_event",
        "conditional": _conditional(values),
        "plural_recipients": (cardinality == "dynamic" and channel in (None, "email"))
        or (cardinality == "multiple" and channel == "email"),
        "summary": values.get("email.content_mode") == "summary" or values.get("goal.action") == "report",
        "has_delay": "delay.duration" in values,
    }
    flags["email_extract"] = kind == "new_email" and flags["conditional"]
    # A backup copies a folder's files, unless an event (a new email, a form...) brings the files itself.
    flags["copies_folder"] = values.get("goal.action") == "backup" and kind in (None, "schedule", "manual")
    # Save File writes a new file unless the files already exist (new-file trigger, folder copy, attachments).
    flags["generated_file"] = (
        channel == "save_file" and kind != "new_file" and not flags["copies_folder"]
        and values.get("save_file.email_content") != "attachments"
    )
    flags["per_record"] = flags["plural_recipients"] and values.get("recipients.source") in LIST_SOURCES
    flags["fixed_recipients"] = not flags["plural_recipients"] or values.get("recipients.source") == "fixed_list"
    flags["digest"] = flags["event_trigger"] and channel == "email" and _wants_digest(values)
    return {f"fact.{name}": YES for name, on in flags.items() if on}


def _conditional(values: dict[str, FieldValue]) -> bool:
    kind = values.get("trigger.kind")
    if kind == "sheet_event":
        return (
            values.get("sheet_trigger.event") == "column_value_changed"
            and values.get("sheet_trigger.condition_mode") == "specific_value"
        )
    return kind == "new_email" and values.get("email_trigger.condition_mode") == "matching_condition"


def _wants_digest(values: dict[str, FieldValue]) -> bool:
    if values.get("email.content_mode") == "summary":
        return True
    subject = str(values.get("email.subject", "")).lower()
    return any(word in subject for word in DIGEST_WORDS)
