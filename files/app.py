import os
import json
import requests
import streamlit as st
from dotenv import load_dotenv
import google.generativeai as genai
from datetime import datetime, timezone

# ---------------- CONFIG ----------------
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyA-iOGmYUxW000Nk6ORFFopi3cJE7J8wA4")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
genai.configure(api_key=GEMINI_API_KEY)

# ---------------- LOAD SERVERS ----------------
def load_servers():
    try:
        with open("servers.json") as f:
            data = json.load(f)
            return data.get("servers", [])
    except Exception as e:
        return [{"name": "default", "url": "http://127.0.0.1:3000/mcp", "description": f"Fallback server: {e}"}]

servers = load_servers()
if "current_server" not in st.session_state:
    st.session_state["current_server"] = servers[0]["url"]

def get_current_server_url():
    return st.session_state.get("current_server", servers[0]["url"])

# ---------------- HELPERS ----------------
def call_mcp_server(method: str, params: dict = None):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    try:
        res = requests.post(
            get_current_server_url(),
            headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
            json=payload,
            timeout=30,
        )
        res.raise_for_status()
        text = res.text.strip()
        if text.startswith("event:"):  # handle SSE-like response
            for line in text.splitlines():
                if line.startswith("data:"):
                    return json.loads(line[len("data:"):].strip())
        return res.json()
    except requests.exceptions.RequestException as e:
        return {"error": f"MCP server request failed: {str(e)}"}

def call_tool(name: str, arguments: dict):
    return call_mcp_server("tools/call", {"name": name, "arguments": arguments})

def sanitize_args(args: dict):
    if not args:
        return {}
    fixed = args.copy()
    if "resource" in fixed and "resourceType" not in fixed:
        fixed["resourceType"] = fixed.pop("resource")
    if fixed.get("resourceType") == "pods" and "namespace" not in fixed:
        fixed["namespace"] = "default"
    if fixed.get("namespace") == "all":
        fixed["allNamespaces"] = True
        fixed.pop("namespace", None)
    return fixed

# ---------------- NEW: ArgoCD application helper ----------------
def try_create_argocd_app(args: dict):
    possible_tool_names = [
        "argocd/create_application",
        "argocd_create_application",
        "applications/create",
        "argocd.applications.create",
        "argocd.create_application",
        "argocd-create-application",
    ]
    last_err = None
    for tname in possible_tool_names:
        resp = call_tool(tname, args)
        if isinstance(resp, dict) and not resp.get("error") and (resp.get("result") is not None or resp):
            return {"tool": tname, "response": resp}
        last_err = {"tool": tname, "response": resp}
    return {"tool": None, "response": last_err}

# ---------------- STREAMLIT APP ----------------
def main():
    st.set_page_config(page_title="MCP Chat Assistant", page_icon="⚡", layout="wide")
    st.title("🤖 Masa Bot Assistant")

    # Sidebar: select MCP server
    st.sidebar.subheader("🌐 Select MCP Server")
    server_names = [f"{s['name']} ({s['url']})" for s in servers]
    choice = st.sidebar.radio("Available Servers:", server_names)
    st.session_state["current_server"] = next(s["url"] for s in servers if choice.startswith(s["name"]))

    # Init message history
    if "messages" not in st.session_state:
        st.session_state["messages"] = []
    if "app_wizard" not in st.session_state:
        st.session_state["app_wizard"] = {"step": 0, "data": {}}

    # --- Wizard flow for "create application" ---
    wizard = st.session_state["app_wizard"]
    if wizard["step"] > 0:
        st.info("🛠 Application Creation Wizard in progress")

    # Chat input
    with st.form("user_input_form", clear_on_submit=True):
        user_input = st.text_input("Type your request (e.g., 'create application')...")
        submitted = st.form_submit_button("Send")

    if submitted and user_input:
        if user_input.lower().strip().startswith("create application") and wizard["step"] == 0:
            wizard["step"] = 1
            st.session_state["messages"].append({"role": "assistant", "content": "Please provide: name"})
        elif wizard["step"] == 1:
            wizard["data"]["name"] = user_input.strip()
            wizard["step"] = 2
            st.session_state["messages"].append({"role": "assistant", "content": "Please provide: repo_url"})
        elif wizard["step"] == 2:
            wizard["data"]["repo_url"] = user_input.strip()
            wizard["step"] = 3
            st.session_state["messages"].append({"role": "assistant", "content": "Please provide: path"})
        elif wizard["step"] == 3:
            wizard["data"]["path"] = user_input.strip()
            wizard["step"] = 4
            st.session_state["messages"].append({"role": "assistant", "content": "Please provide: dest_ns"})
        elif wizard["step"] == 4:
            wizard["data"]["dest_ns"] = user_input.strip()
            # All inputs collected → create app
            args = {
                "name": wizard["data"]["name"],
                "project": "default",
                "syncPolicy": "Automatic",
                "source": {
                    "repoURL": wizard["data"]["repo_url"],
                    "path": wizard["data"]["path"],
                    "targetRevision": "HEAD",
                },
                "destination": {
                    "server": "https://kubernetes.default.svc",
                    "namespace": wizard["data"]["dest_ns"],
                },
            }
            args = sanitize_args(args)
            result = try_create_argocd_app(args)
            if result.get("tool"):
                st.success(f"✅ Application created with `{result['tool']}`")
                st.write(result["response"])
            else:
                st.error("❌ Failed to create application")
                st.write(result["response"])
            wizard["step"] = 0
            wizard["data"] = {}
        else:
            st.session_state["messages"].append({"role": "user", "content": user_input})

    # Show chat history
    for msg in st.session_state["messages"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

if __name__ == "__main__":
    main()
