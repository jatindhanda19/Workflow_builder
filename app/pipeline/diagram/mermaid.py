"""Mermaid flowchart of a generated workflow.

Mermaid reserves words such as "end", "graph" and "default", so class names avoid them and
every node id gets a prefix.
"""

from typing import Any

CLASS_DEFS = {
    "trigger": "fill:#dbeafe,stroke:#2563eb,color:#0f172a,stroke-width:2px",
    "condition": "fill:#fef3c7,stroke:#d97706,color:#0f172a,stroke-width:2px",
    "action": "fill:#fce7f3,stroke:#db2777,color:#0f172a,stroke-width:2px",
    "stop": "fill:#e5e7eb,stroke:#6b7280,color:#0f172a",
}
ICONS = {"trigger": "⚡", "condition": "🔀", "action": "▶", "stop": "⏹"}
SHAPES = {"trigger": ("([", "])"), "condition": ("{", "}"), "action": ("(", ")"), "stop": ("((", "))")}
ID_PREFIX = "n_"


def to_mermaid(workflow: dict[str, Any]) -> str:
    lines = ["flowchart LR"]
    for node in workflow["nodes"]:
        category = _category(node)
        open_, close = SHAPES[category]
        lines.append(f'  {ID_PREFIX}{node["id"]}{open_}"{ICONS[category]} {_escape(node["name"])}"{close}:::{category}')
    for edge in workflow["edges"]:
        label = {"true": "|Yes|", "false": "|No|"}.get(edge.get("branch") or "", "")
        lines.append(f'  {ID_PREFIX}{edge["from"]} -->{label} {ID_PREFIX}{edge["to"]}')
    lines += [f"  classDef {name} {style}" for name, style in CLASS_DEFS.items()]
    return "\n".join(lines)


def _category(node: dict[str, Any]) -> str:
    if node["type"] == "end":
        return "stop"
    if node["type"] == "condition":
        return "condition"
    return "trigger" if node["type"].endswith("_trigger") else "action"


def _escape(text: str) -> str:
    return text.replace('"', "#quot;").replace("<", "#lt;").replace(">", "#gt;").replace("{", "#123;").replace("}", "#125;")
