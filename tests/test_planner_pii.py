"""What the planner sends to the LLM: identity numbers are removed, workflow values such as emails are kept."""

from app.pipeline.planner import build_context
from app.state.models import Message, WorkflowState


def _context(message):
    return build_context(WorkflowState(latest_user_message=message, messages=[Message(role="user", content=message)]))


def test_planner_context_redacts_identity_numbers():
    context = _context("Save the customer with Aadhaar 2345 6789 0123 and PAN ABCDE1234F to Airtable")

    assert "2345 6789 0123" not in context and "ABCDE1234F" not in context
    assert "[AADHAAR_LIKE]" in context and "[PAN_LIKE]" in context


def test_planner_context_keeps_emails_and_phone_numbers_a_workflow_needs():
    context = _context("Send a notification to sakshi@example.com and an SMS to 9876543210")

    assert "sakshi@example.com" in context and "9876543210" in context


def test_planner_context_preserves_normal_text():
    assert "Create a workflow every Monday" in _context("Create a workflow every Monday")
