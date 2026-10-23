from app.pipeline.planner import build_context
from app.state.models import Message, WorkflowState


def test_planner_context_redacts_email():
    state = WorkflowState(
        latest_user_message="Send a notification to sakshi@example.com",
        messages=[
            Message(
                role="user",
                content="Send a notification to sakshi@example.com",
            )
        ],
    )

    context = build_context(state)

    assert "sakshi@example.com" not in context
    assert "[EMAIL]" in context


def test_planner_context_preserves_normal_text():
    state = WorkflowState(
        latest_user_message="Create a workflow every Monday",
        messages=[],
    )

    context = build_context(state)

    assert "Create a workflow every Monday" in context