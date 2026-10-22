"""
Security guardrails for generated workflows.

Checks graph structure before a workflow is accepted.
Existing JSON-schema and grounding validation remain in
app.pipeline.generation.checks.
"""

from dataclasses import dataclass

from app.pipeline.generation.schema import Workflow


@dataclass(frozen=True)
class WorkflowGuardrailResult:
    allowed: bool
    errors: tuple[str, ...]


def validate_workflow(workflow: Workflow) -> WorkflowGuardrailResult:
    """
    Validate workflow graph structure.

    Checks:
    - At least one node exists.
    - Node IDs are unique.
    - Exactly one trigger exists, and it is the first node.
    - Every edge references existing nodes.
    - Condition nodes have true and false branches.
    - Non-condition nodes do not have branches.
    - Every node is reachable from the trigger.
    - The graph contains no cycles.
    """

    errors: list[str] = []
    nodes = workflow.nodes
    edges = workflow.edges

    if not nodes:
        return WorkflowGuardrailResult(
            allowed=False,
            errors=("The workflow has no nodes.",),
        )

    node_ids = [node.id for node in nodes]
    known_ids = set(node_ids)

    # Check duplicate IDs.
    seen: set[str] = set()
    for node_id in node_ids:
        if node_id in seen:
            errors.append(f"Duplicate node ID: {node_id}")
        seen.add(node_id)

    # Validate trigger placement.
    triggers = [
        node for node in nodes
        if node.type.endswith("_trigger")
    ]

    if len(triggers) != 1:
        errors.append("The workflow must contain exactly one trigger.")
    elif nodes[0].id != triggers[0].id:
        errors.append("The trigger must be the first node.")

    # Build the graph using only valid edge references.
    children: dict[str, list[str]] = {
        node_id: [] for node_id in known_ids
    }
    branches: dict[str, list[str | None]] = {
        node_id: [] for node_id in known_ids
    }

    for edge in edges:
        if edge.source not in known_ids:
            errors.append(
                f"Edge source '{edge.source}' does not exist."
            )
            continue

        if edge.target not in known_ids:
            errors.append(
                f"Edge target '{edge.target}' does not exist."
            )
            continue

        children[edge.source].append(edge.target)
        branches[edge.source].append(edge.branch)

    # Validate condition branches.
    for node in nodes:
        node_branches = branches[node.id]

        if node.type == "condition":
            if sorted(node_branches, key=lambda b: b or "") != [
                "false",
                "true",
            ]:
                errors.append(
                    f"Condition '{node.id}' must have exactly "
                    "one true branch and one false branch."
                )

        elif any(branch is not None for branch in node_branches):
            errors.append(
                f"Only condition nodes may have branches: '{node.id}'."
            )

    # Check reachability when the first node is the trigger.
    if triggers and len(triggers) == 1 and nodes[0].id == triggers[0].id:
        reachable: set[str] = set()
        stack = [triggers[0].id]

        while stack:
            current = stack.pop()

            if current in reachable:
                continue

            reachable.add(current)
            stack.extend(children.get(current, []))

        for node_id in node_ids:
            if node_id not in reachable:
                errors.append(
                    f"Node '{node_id}' is not reachable from the trigger."
                )

    # Detect cycles using depth-first search.
    state: dict[str, int] = {}

    def has_cycle(node_id: str) -> bool:
        if state.get(node_id) == 1:
            return True

        if state.get(node_id) == 2:
            return False

        state[node_id] = 1

        for child in children.get(node_id, []):
            if has_cycle(child):
                return True

        state[node_id] = 2
        return False

    cycle_found = any(
        has_cycle(node_id)
        for node_id in node_ids
        if state.get(node_id, 0) == 0
    )

    if cycle_found:
        errors.append("The workflow contains a cycle.")

    return WorkflowGuardrailResult(
        allowed=not errors,
        errors=tuple(errors),
    )
