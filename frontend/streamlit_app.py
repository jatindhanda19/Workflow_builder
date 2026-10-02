import html
import json
import os
import uuid

import requests
import streamlit as st
import streamlit.components.v1 as components

API_URL = os.getenv("API_URL", "http://127.0.0.1:8001")
REQUEST_TIMEOUT = 90
MERMAID_JS = "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs"
DIAGRAM_HEIGHT = 260

MODE_LABELS = {
    "collecting": ("Collecting information", "🟡"),
    "ready": ("Workflow generated", "🟢"),
    "post_generation": ("Workflow generated", "🟢"),
}
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
STATUS_ICONS = {"filled": "✅ filled", "ambiguous": "⚠️ ambiguous", "missing": "❌ missing"}
RUN_ICONS = {"ok": "✅", "failed": "❌", "skipped": "⏭️"}

st.set_page_config(page_title="Workflow Builder", page_icon="🧩", layout="wide")


def init_state() -> None:
    st.session_state.setdefault("session_id", uuid.uuid4().hex)
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("view", None)


def reset_conversation() -> None:
    old_id = st.session_state.get("session_id")
    if old_id:
        try:
            requests.delete(f"{API_URL}/sessions/{old_id}", timeout=10)
        except requests.RequestException:
            pass
    for key in ("session_id", "messages", "view", "picked"):
        st.session_state.pop(key, None)
    init_state()


def send_message(text: str) -> None:
    st.session_state.messages.append({"role": "user", "content": text})
    try:
        response = requests.post(
            f"{API_URL}/chat",
            json={"session_id": st.session_state.session_id, "message": text},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except requests.HTTPError as exc:
        try:
            detail = exc.response.json().get("detail", str(exc))
        except ValueError:
            detail = f"{exc.response.status_code} {exc.response.text[:200]}"
        st.session_state.messages.append({"role": "assistant", "content": f"Backend error: {detail}"})
        return
    except requests.RequestException as exc:
        st.session_state.messages.append({"role": "assistant", "content": f"Cannot reach the backend: {exc}"})
        return

    view = response.json()
    st.session_state.view = view
    st.session_state.messages.append({"role": "assistant", "content": view["reply"]})


def pick_option(label: str) -> None:
    st.session_state.picked = label


def render_options(question: dict | None) -> None:
    """The current question's options as buttons; a click sends that option as the answer."""
    if not question or not question["options"]:
        return
    for i, label in enumerate(question["options"]):
        st.button(f"{LETTERS[i]}. {label}", key=f"option-{len(st.session_state.messages)}-{i}",
                  on_click=pick_option, args=(label,), use_container_width=True)
    if question["allow_custom"]:
        st.caption("Or type your own answer below.")


def _show(value) -> str:
    if value is None:
        return ""
    return ", ".join(value) if isinstance(value, list) else str(value)


def render_sidebar(view: dict | None) -> None:
    with st.sidebar:
        st.header("Collected information")
        mode = view["mode"] if view else "collecting"
        label, icon = MODE_LABELS[mode]
        st.markdown(f"**{icon} {label}**")
        if view is None:
            st.caption("Nothing collected yet.")
        else:
            if view["all_collected"]:
                st.success("All information collected ✅")
            elif view.get("asking_field"):
                st.caption(f"Asking about: `{view['asking_field']}`")
            rows = [
                {
                    "Parameter": f"{row['node']} · {row['parameter']}",
                    "Value": _show(row["value"]),
                    "Status": STATUS_ICONS[row["status"]],
                    "Note": row["note"] or "",
                }
                for row in view["fields"]
            ]
            if rows:
                st.dataframe(rows, hide_index=True, use_container_width=True)
        st.button("New conversation", on_click=reset_conversation, use_container_width=True)


def render_diagram(diagram: str) -> None:
    """The Mermaid flowchart from the backend, drawn in the browser by mermaid.js."""
    components.html(
        f"""
        <div class="mermaid" style="background:#ffffff;border-radius:8px;padding:16px;text-align:center">
        {html.escape(diagram)}
        </div>
        <script type="module">
          import mermaid from "{MERMAID_JS}";
          mermaid.initialize({{ startOnLoad: true, theme: "neutral", securityLevel: "strict" }});
        </script>
        """,
        height=DIAGRAM_HEIGHT,
        scrolling=True,
    )


def render_workflow(workflow: dict, diagram: str | None, editing: bool = False) -> None:
    st.divider()
    meta = workflow["metadata"]
    st.subheader(f"Workflow: {meta['name']}")
    st.caption(meta["trigger_summary"])
    if editing:
        st.info("Showing the last generated version. It updates once your changes are complete.")
    render_workflow_body(workflow, diagram)
    render_run(meta["created_at"])


def render_run(version: str) -> None:
    """Runs the current workflow on the backend and shows each step's result."""
    st.markdown("**Run**")
    st.caption("Runs Manual or Webhook triggers, If, HTTP Request and Slack messages. "
               "Slack uses SLACK_WEBHOOK_URL from the backend's environment.")
    raw = st.text_area("Trigger data (JSON)", value="{}", key=f"run-input-{version}", height=100)
    if st.button("Run", key=f"run-{version}"):
        try:
            data = json.loads(raw or "{}")
        except ValueError:
            data = None
        if not isinstance(data, dict):
            st.error("The trigger data must be a JSON object, e.g. {\"amount\": 120000}.")
            return
        try:
            response = requests.post(f"{API_URL}/sessions/{st.session_state.session_id}/run",
                                     json={"input": data}, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            st.error(f"Cannot reach the backend: {exc}")
            return
        if not response.ok:
            st.error(response.json().get("detail", response.text) if "json" in response.headers.get(
                "content-type", "") else response.text)
            return
        st.session_state[f"run-result-{version}"] = response.json()["steps"]
    for step in st.session_state.get(f"run-result-{version}", []):
        icon = RUN_ICONS[step["status"]]
        st.markdown(f"{icon} **{step['name']}** `{step['id']}` · {step['status']}"
                    + (f": {step['error']}" if step["error"] else ""))
        if step["output"] is not None:
            st.json(step["output"], expanded=False)


def render_previous_workflows(previous: list[dict]) -> None:
    """Workflows generated earlier in this session, newest first."""
    if not previous:
        return
    st.divider()
    st.subheader("Previous workflows")
    for number, item in reversed(list(enumerate(previous, 1))):
        meta = item["workflow"]["metadata"]
        with st.expander(f"{number}. {meta['name']} · {meta['created_at']}"):
            st.caption(meta["trigger_summary"])
            render_workflow_body(item["workflow"], item["diagram"])


def render_workflow_body(workflow: dict, diagram: str | None) -> None:
    if diagram:
        st.markdown("**Flowchart**")
        render_diagram(diagram)

    steps, raw = st.columns(2)
    with steps:
        st.markdown("**Steps**")
        for node in workflow["nodes"]:
            st.markdown(f"**{node['name']}** `{node['id']}` ({node['type']})")
            if node["parameters"]:
                st.json(node["parameters"], expanded=False)
        st.markdown("**Edges**")
        for edge in workflow["edges"]:
            branch = f" ({'yes' if edge['branch'] == 'true' else 'no'})" if edge.get("branch") else ""
            st.markdown(f"`{edge['from']}` → `{edge['to']}`{branch}")
    with raw:
        st.markdown("**JSON**")
        st.code(json.dumps(workflow, indent=2, ensure_ascii=False), language="json")


def main() -> None:
    init_state()
    st.title("Conversational Workflow Builder")
    st.caption("Describe an automation. I will ask question I need and never guess it.")

    prompt = st.chat_input("Describe the workflow you want to build, or answer the question")
    prompt = prompt or st.session_state.pop("picked", None)
    if prompt:
        with st.spinner("Thinking..."):
            send_message(prompt)

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    view = st.session_state.view
    if view:
        render_options(view.get("question"))
    render_sidebar(view)
    if view and view["workflow"]:
        render_workflow(view["workflow"], view.get("diagram"), editing=view["mode"] == "collecting")
    if view:
        render_previous_workflows(view.get("previous_workflows", []))


main()
