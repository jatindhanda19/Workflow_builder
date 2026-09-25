"""Part D test 10: requests unlike the notify template produce sensible plans with no leakage."""

from tests.conftest import Conversation, v

NOTIFY_FIELDS = ("email.", "slack.", "recipients.", "email_trigger.", "condition.")


def run(convo: Conversation, opening: str, intent: dict, extraction: dict, answers: list[tuple[str, str]]) -> None:
    convo.say(opening, extraction, intent=intent)
    for expected_field, answer in answers:
        assert convo.asking == expected_field, (convo.asking, expected_field)
        convo.say(answer)
    assert convo.state.mode == "ready", convo.reply


def test_backup_drive_files_weekly(convo: Conversation) -> None:
    run(convo, "backup my Drive files weekly",
        {"goal_action": "backup", "trigger_hint": "schedule", "trigger_evidence": "weekly", "entities": ["file"]},
        {"values": [v("schedule_trigger.frequency", "weekly"), v("file_source.storage", "google_drive", "Drive")]},
        [("schedule_trigger.day_of_week", "Sunday"), ("schedule_trigger.time", "11 pm"),
         ("schedule_trigger.timezone", "UTC"), ("file_source.folder", "all files"),
         ("save_file.storage", "Dropbox"), ("save_file.folder", "Drive Backups")])
    assert [n.type for n in convo.state.workflow.nodes] == ["schedule_trigger", "list_files", "save_file"]
    assert convo.value("action.channel") == "save_file"
    assert not any(field.startswith(NOTIFY_FIELDS) for field in convo.asked())
    assert convo.state.workflow.metadata.name == "Back Up Google Drive Files"


def test_post_slack_message_every_monday(convo: Conversation) -> None:
    run(convo, "post a Slack message every Monday",
        {"goal_action": "post_message", "trigger_hint": "schedule", "trigger_evidence": "every Monday"},
        {"values": [v("action.channel", "slack", "Slack message"), v("schedule_trigger.frequency", "weekly", "every Monday"),
                    v("schedule_trigger.day_of_week", "monday", "Monday")]},
        [("schedule_trigger.time", "9 am"), ("schedule_trigger.timezone", "IST"), ("slack.workspace", "Acme"),
         ("slack.channel", "#general"), ("slack.message", "Good morning team, standup is at 10!")])
    assert [n.type for n in convo.state.workflow.nodes] == ["schedule_trigger", "slack_message"]
    assert convo.state.workflow.nodes[1].parameters["message"] == "Good morning team, standup is at 10!"
    assert not any(field.startswith(("email.", "recipients.", "dedupe.")) for field in convo.asked())


def test_add_form_responses_to_a_sheet(convo: Conversation) -> None:
    run(convo, "add new form responses to a sheet",
        {"goal_action": "sync_data", "trigger_hint": "form_submission", "trigger_evidence": "new form responses"},
        {"values": [v("action.channel", "sheet_row", "to a sheet")]},
        [("form_trigger.platform", "Google Forms"), ("form_trigger.form_name", "Event signup"),
         ("sheet_row.platform", "Google Sheets"), ("sheet_row.file_name", "Signups"),
         ("sheet_row.tab_name", "Responses"), ("sheet_row.operation", "add a new row"),
         ("sheet_row.columns", "all fields"), ("dedupe.mode", "yes")])
    types = [n.type for n in convo.state.workflow.nodes]
    assert types == ["google_forms_trigger", "dedupe", "sheet_append_row"]
    assert not any(field.startswith(NOTIFY_FIELDS) for field in convo.asked())
