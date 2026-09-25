from app.pipeline.diagram.builder import ICONS, Diagram, DiagramEdge, DiagramNode

CLASS_DEFS = {
    "trigger": "fill:#dbeafe,stroke:#2563eb,color:#0f172a,stroke-width:2px",
    "logic": "fill:#fef3c7,stroke:#d97706,color:#0f172a,stroke-width:2px",
    "data": "fill:#dcfce7,stroke:#16a34a,color:#0f172a,stroke-width:2px",
    "action": "fill:#fce7f3,stroke:#db2777,color:#0f172a,stroke-width:2px",
    "pending": "fill:#e5e7eb,stroke:#9ca3af,color:#6b7280,stroke-dasharray:5 4",
}
MAX_SUBTITLE = 48


def to_mermaid(diagram: Diagram) -> str:
    lines = ["flowchart LR"]
    in_loop = set(diagram.loop[1]) if diagram.loop else set()
    lines += [f"  {_node(n)}" for n in diagram.nodes if n.id not in in_loop]
    if diagram.loop and in_loop:
        item = next((n.parameters.get("item", "item") for n in diagram.nodes if n.id == diagram.loop[0]), "item")
        lines.append(f'  subgraph loop_scope["For each {_escape(str(item))}"]')
        lines.append("    direction LR")
        lines += [f"    {_node(n)}" for n in diagram.nodes if n.id in in_loop]
        lines.append("  end")
    lines += [f"  {_edge(e)}" for e in diagram.edges]
    lines += [f"  classDef {name} {style}" for name, style in CLASS_DEFS.items()]
    lines += [f'  click {n.id} showNode "Show parameters"' for n in diagram.nodes]
    return "\n".join(lines)


def _node(node: DiagramNode) -> str:
    icon = "⏹" if node.type == "end" else ICONS[node.category]
    label = f"{icon} {_escape(node.name)}"
    if node.subtitle:
        label += f"<br/>{_escape(_shorten(node.subtitle))}"
    open_, close = _shape(node)
    css = "pending" if node.pending else node.category
    return f'{node.id}{open_}"{label}"{close}:::{css}'


def _shape(node: DiagramNode) -> tuple[str, str]:
    if node.type == "end":
        return "((", "))"
    if node.type == "condition":
        return "{", "}"
    return {"trigger": ("([", "])"), "logic": ("{{", "}}"), "data": ("[/", "/]"), "action": ("(", ")")}[node.category]


def _edge(edge: DiagramEdge) -> str:
    arrow = "-.->" if edge.dashed else "-->"
    label = f"|{_escape(edge.label)}|" if edge.label else ""
    return f"{edge.source} {arrow}{label} {edge.target}"


def _escape(text: str) -> str:
    return text.replace('"', "#quot;").replace("<", "#lt;").replace(">", "#gt;").replace("{", "#123;").replace("}", "#125;")


def _shorten(text: str) -> str:
    return text if len(text) <= MAX_SUBTITLE else text[: MAX_SUBTITLE - 1] + "…"
