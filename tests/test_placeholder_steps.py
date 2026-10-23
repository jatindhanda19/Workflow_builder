"""Session 9f24e830: picking a described trigger ("When I upload a PDF to Dropbox") for an app question stored the
sentence as the app, and the action kept the placeholder operation "Do something with the result" all the way into
the generated workflow."""

from test_clarification import _run

from app.pipeline.planner import build_context
from app.pipeline.planner_schema import NextQuestion, PlannedParam, PlannedStep, TurnPlan
from app.state.models import ACTION_PLACEHOLDER, WorkflowState

DESCRIBED = ["When I upload a PDF to Dropbox", "When a new paper is added to a folder"]


def _plan(trigger_app=None, trigger_op="", action_app=None, action_op=ACTION_PLACEHOLDER, target=None, options=()):
    question = NextQuestion(target=target, question="Which one?", options=list(options)) if target else None
    return TurnPlan(message_kind="build", next_question=question, steps=[
        PlannedStep(id="trigger", kind="trigger", app=trigger_app, app_evidence=trigger_app, operation=trigger_op),
        PlannedStep(id="action", kind="action", app=action_app, app_evidence=action_app, operation=action_op,
                    params=[PlannedParam(name="folder", label="Folder", required=False)]),
    ])


def _asked_trigger_app():
    """Turn 1: a vague request; the planner's options for the trigger's app are descriptions, not app names."""
    return _run(WorkflowState(), "Build a workflow to summarize research papers.",
                _plan(target="trigger.app", options=DESCRIBED))


def test_a_picked_description_gives_the_app_it_names_not_the_sentence():
    state = _asked_trigger_app()
    assert state.target == "trigger.app" and [o.label for o in state.question.options][:2] == DESCRIBED
    state = _run(state, "When I upload a PDF to Dropbox", _plan(trigger_app="Dropbox", trigger_op="New file"))
    assert state.picked == "When I upload a PDF to Dropbox"
    assert state.step("trigger").app == "Dropbox"


def test_a_picked_description_naming_no_app_does_not_become_the_app():
    state = _run(_asked_trigger_app(), "When a new paper is added to a folder", _plan(trigger_op="New file"))
    assert state.step("trigger").app is None and state.target == "trigger.app"


def test_a_placeholder_operation_is_asked_and_blocks_generation():
    state = _run(_asked_trigger_app(), "When I upload a PDF to Dropbox", _plan(trigger_app="Dropbox", trigger_op="New file"))
    # The user names the action's app; the planner copies the placeholder operation back.
    state = _run(state, "Google Docs", _plan("Dropbox", "New file", action_app="Google Docs"))
    assert state.step("action").app == "Google Docs"
    assert state.step("action").operation == ACTION_PLACEHOLDER
    assert state.workflow is None and state.mode == "collecting"
    assert state.target == "action.operation"
    assert state.question.text == "What should Google Docs do in this step?"

    state = _run(state, "Create a document with the summary", _plan("Dropbox", "New file", action_app="Google Docs"))
    assert state.step("action").operation == "Create a document with the summary"
    assert state.workflow is not None  # nothing else was missing

    # A later plan that copies the placeholder back does not undo the answer.
    state = _run(state, "thanks", _plan("Dropbox", "New file", action_app="Google Docs"))
    assert state.step("action").operation == "Create a document with the summary"


def test_a_picked_operation_option_is_used():
    state = _run(_asked_trigger_app(), "When I upload a PDF to Dropbox", _plan(trigger_app="Dropbox", trigger_op="New file"))
    state = _run(state, "Google Docs", _plan("Dropbox", "New file", action_app="Google Docs", target="action.operation",
                                             options=["Create document", "Append text"]))
    assert [o.label for o in state.question.options] == ["Create document", "Append text"]
    state = _run(state, "B", _plan("Dropbox", "New file", action_app="Google Docs"))
    assert state.step("action").operation == "Append text"


def test_the_planner_is_not_shown_placeholder_operations():
    state = _asked_trigger_app()
    context = build_context(state)
    assert ACTION_PLACEHOLDER not in context and '"id":"action"' in context
