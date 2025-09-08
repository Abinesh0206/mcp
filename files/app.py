import os
import json
import requests
import streamlit as st
from dotenv import load_dotenv
import google.generativeai as genai
from datetime import datetime, timezone

# ---------------- CONFIG ----------------
load_dotenv()

# Load servers.json config
with open("servers.json") as f:
    CONFIG = json.load(f)

SERVERS = {s["name"]: s for s in CONFIG["servers"]}
ROUTING = CONFIG["routing"]

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyA-iOGmYUxW000Nk6ORFFopi3cJE7J8wA4")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

genai.configure(api_key=GEMINI_API_KEY)

# ---------------- HELPERS ----------------
def resolve_server(query: str) -> dict:
    """Pick correct MCP server based on query and routing rules."""
    for rule in ROUTING:
        if any(word in query.lower() for word in rule["matcher"].split("|")):
            return SERVERS.get(rule["server"])
    return SERVERS.get("kubernetes")  # default fallback


def call_mcp_server(method: str, params: dict = None, server: dict = None):
    """Call MCP server dynamically based on routing."""
    if not server:
        server = SERVERS.get("kubernetes")

    # Replace ${TOKEN} placeholders with env values
    auth_header = server.get("authHeader", "")
    if "${" in auth_header:
        token_name = auth_header.strip("${}").replace("Bearer ", "")
        token_value = os.getenv(token_name, "")
        auth_header = f"Bearer {token_value}" if token_value else ""

    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    try:
        res = requests.post(
            f"{server['baseUrl']}/mcp",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Authorization": auth_header,
            },
            json=payload,
            timeout=30,
        )
        res.raise_for_status()
        text = res.text.strip()
        if text.startswith("event:"):  # stream response
            for line in text.splitlines():
                if line.startswith("data:"):
                    return json.loads(line[len("data:"):].strip())
        return res.json()
    except requests.exceptions.RequestException as e:
        return {"error": f"MCP server request failed: {str(e)}"}


def list_mcp_tools(server: dict):
    resp = call_mcp_server("tools/list", server=server)
    if "result" in resp and isinstance(resp["result"], dict):
        return resp["result"].get("tools", [])
    return []


def call_tool(name: str, arguments: dict, server: dict):
    if not name or not isinstance(arguments, dict):
        return {"error": "Invalid tool name or arguments"}
    return call_mcp_server("tools/call", {"name": name, "arguments": arguments}, server)


def humanize_age(created_at: str) -> str:
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - created
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return f"{seconds}s"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes}m"
        hours = minutes // 60
        if hours < 24:
            return f"{hours}h{minutes%60}m"
        days = hours // 24
        hours = hours % 24
        return f"{days}d{hours}h"
    except Exception:
        return "-"


def ask_gemini(prompt: str):
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Gemini error: {str(e)}"


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


# ---------------- STREAMLIT APP ----------------
def main():
    st.set_page_config(page_title="MCP Chat Assistant", page_icon="⚡", layout="wide")
    st.title("🤖Masa Bot Assistant")

    if "messages" not in st.session_state:
        st.session_state["messages"] = []

    # Sidebar tools per server
    st.sidebar.subheader("🔧 Available MCP Tools by Server")
    for sname, server in SERVERS.items():
        st.sidebar.write(f"### {sname}")
        tools = list_mcp_tools(server)
        if tools:
            for t in tools:
                st.sidebar.write(f"- {t['name']}: {t.get('description','')}")
        else:
            st.sidebar.error(f"⚠️ Could not fetch tools from {sname} MCP.")

    # Display chat history
    for msg in st.session_state["messages"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Chat input
    with st.form("user_input_form", clear_on_submit=True):
        user_input = st.text_input("Ask something (K8s, Jenkins, ArgoCD)...")
        submitted = st.form_submit_button("Send")
        if submitted and user_input:
            st.session_state["messages"].append({"role": "user", "content": user_input})
            st.chat_message("user").markdown(user_input)

            # Decide server
            server = resolve_server(user_input)

            # Call simple tool (example: list namespaces)
            if "namespace" in user_input.lower():
                resp = call_tool("kubectl_get", {"resourceType": "namespaces"}, server)
            else:
                resp = {"note": "No matching tool, fallback AI answer."}

            pretty_answer = ask_gemini(
                f"User asked: {user_input}\n\n"
                f"Here is the raw response from {server['name']} MCP:\n{json.dumps(resp, indent=2)}\n\n"
                f"Answer in natural human-friendly language."
            )
            st.session_state["messages"].append({"role": "assistant", "content": pretty_answer})
            st.chat_message("assistant").markdown(pretty_answer)


if __name__ == "__main__":
    main()
