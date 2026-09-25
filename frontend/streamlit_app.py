import json
import os
import uuid

import requests
import streamlit as st

API_URL = os.getenv("API_URL", "http://127.0.0.1:8001")
REQUEST_TIMEOUT = 90

MODE_LABELS = {
    "collecting": ("Collecting information", "🟡"),
    "ready": ("Workflow generated", "🟢"),
    "post_generation": ("Workflow generated", "🟢"),
}
STATUS_ICONS = {"filled": "✅ filled", "ambiguous": "⚠️ ambiguous", "missing": "❌ missing"}

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
    for key in ("session_id", "messages", "view"):
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


def render_workflow(workflow: dict) -> None:
    st.divider()
    meta = workflow["metadata"]
    st.subheader(f"Workflow: {meta['name']}")
    st.caption(meta["trigger_summary"])

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
    st.caption("Describe an automation. I will ask for anything I need and never guess it.")

    prompt = st.chat_input("Describe the workflow you want to build")
    if prompt:
        with st.spinner("Thinking..."):
            send_message(prompt)

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    view = st.session_state.view
    render_sidebar(view)
    if view and view["workflow"]:
        render_workflow(view["workflow"])


main()
