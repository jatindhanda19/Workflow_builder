from datetime import datetime, timezone

from app.pipeline.generation.schema import (
    Edge,
    Workflow,
    WorkflowMetadata,
    WorkflowNode,
)
from app.security.workflow_guardrails import validate_workflow


def make_workflow(nodes, edges):
    return Workflow(
        metadata=WorkflowMetadata(
            name="Test workflow",
            trigger_summary="Manual trigger",
            description="Test workflow description",
            created_at=datetime.now(timezone.utc).isoformat(),
        ),
        nodes=nodes,
        edges=edges,
    )


def make_node(node_id, node_type, name=None, parameters=None):
    return WorkflowNode(
        id=node_id,
        type=node_type,
        name=name or node_id,
        parameters=parameters or {},
    )


def test_valid_workflow_is_allowed():
    workflow = make_workflow(
        [
            make_node(
                "manual",
                "manual_trigger",
                parameters={"app": "manual"},
            ),
            make_node("finish", "end"),
        ],
        [Edge(source="manual", target="finish")],
    )

    result = validate_workflow(workflow)

    assert result.allowed is True
    assert result.errors == ()


def test_empty_workflow_is_rejected():
    workflow = make_workflow([], [])

    result = validate_workflow(workflow)

    assert result.allowed is False
    assert "The workflow has no nodes." in result.errors


def test_duplicate_node_ids_are_rejected():
    workflow = make_workflow(
        [
            make_node("manual", "manual_trigger"),
            make_node("manual", "end"),
        ],
        [],
    )

    result = validate_workflow(workflow)

    assert result.allowed is False
    assert any("Duplicate node ID" in error for error in result.errors)


def test_unknown_edge_target_is_rejected():
    workflow = make_workflow(
        [make_node("manual", "manual_trigger")],
        [Edge(source="manual", target="missing")],
    )

    result = validate_workflow(workflow)

    assert result.allowed is False
    assert any("does not exist" in error for error in result.errors)


def test_trigger_must_be_first():
    workflow = make_workflow(
        [
            make_node("finish", "end"),
            make_node("manual", "manual_trigger"),
        ],
        [Edge(source="manual", target="finish")],
    )

    result = validate_workflow(workflow)

    assert result.allowed is False
    assert any("first node" in error for error in result.errors)


def test_unreachable_node_is_rejected():
    workflow = make_workflow(
        [
            make_node("manual", "manual_trigger"),
            make_node("finish", "end"),
            make_node("orphan", "some_action"),
        ],
        [Edge(source="manual", target="finish")],
    )

    result = validate_workflow(workflow)

    assert result.allowed is False
    assert any("not reachable" in error for error in result.errors)


def test_cycle_is_rejected():
    workflow = make_workflow(
        [
            make_node("manual", "manual_trigger"),
            make_node("action", "some_action"),
        ],
        [
            Edge(source="manual", target="action"),
            Edge(source="action", target="manual"),
        ],
    )

    result = validate_workflow(workflow)

    assert result.allowed is False
    assert any("cycle" in error for error in result.errors)


def test_condition_requires_both_branches():
    workflow = make_workflow(
        [
            make_node("manual", "manual_trigger"),
            make_node("check", "condition"),
            make_node("finish", "end"),
        ],
        [
            Edge(source="manual", target="check"),
            Edge(source="check", target="finish", branch="true"),
        ],
    )

    result = validate_workflow(workflow)

    assert result.allowed is False
    assert any("true branch" in error for error in result.errors)
