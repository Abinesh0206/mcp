import os
import json
import requests
import streamlit as st
from dotenv import load_dotenv
import google.generativeai as genai
from datetime import datetime, timezone
import re

# ---------------- CONFIG ----------------
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyA-iOGmYUxW000Nk6ORFFopi3cJE7J8wA4")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
genai.configure(api_key=GEMINI_API_KEY)

# Load servers.json
with open("servers.json") as f:
    config = json.load(f)

SERVERS = {s["name"]: s for s in config["servers"]}
ROUTING = config["routing"]

# ---------------- HELPERS ----------------
def pick_server(query: str) -> str:
    """Choose which MCP server to use based on routing rules"""
    for rule in ROUTING:
        if re.search(rule["matcher"], query, re.IGNORECASE):
            return rule["server"]
    return "kubernetes"  # default fallback

def call_mcp_server(server: str, method: str, params: dict = None):
    srv = SERVERS.get(server)
    if not srv:
        return {"error": f"Server '{server}' not found"}

    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    try:
        headers = {"Content-Type": "application/json"}
        if "authHeader" in srv and srv["authHeader"]:
            token = os.path.expandvars(srv["authHeader"].replace("Bearer ", ""))
            headers["Authorization"] = f"Bearer {token}"

        res = requests.post(
            srv["baseUrl"],
            headers=headers,
            json=payload,
            timeout=30,
        )
        res.raise_for_status()
        text = res.text.strip()
        if text.startswith("event:"):
            for line in text.splitlines():
                if line.startswith("data:"):
                    return json.loads(line[len("data:"):].strip())
        return res.json()
    except requests.exceptions.RequestException as e:
        return {"error": f"MCP server '{server}' request failed: {str(e)}"}

def list_mcp_tools(server: str):
    resp = call_mcp_server(server, "tools/list")
    if "result" in resp and isinstance(resp["result"], dict):
        return resp["result"].get("tools", [])
    return []

def call_tool(server: str, name: str, arguments: dict):
    if not name or not isinstance(arguments, dict):
        return {"error": "Invalid tool name or arguments"}
    return call_mcp_server(server, "tools/call", {"name": name, "arguments": arguments})

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
    st.title("🤖 MCP Client – Kubernetes / Jenkins / ArgoCD Assistant")

    if "messages" not in st.session_state:
        st.session_state["messages"] = []

    # Sidebar tools per server
    for srv in SERVERS.keys():
        tools = list_mcp_tools(srv)
        if tools:
            st.sidebar.subheader(f"🔧 {srv} Tools")
            for t in tools:
                st.sidebar.write(f"- {t['name']}: {t.get('description','')}")
        else:
            st.sidebar.warning(f"⚠ Could not fetch tools from {srv} MCP.")

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

            # Decide which server
            server = pick_server(user_input)

            # Call Gemini to decide tool
            decision = ask_gemini(f"""
You are an AI agent that maps queries to tools.

User query: "{user_input}"
Available server: {server}
Available tools: {list_mcp_tools(server)}

Return JSON:
{{
  "tool": "<tool name or null>",
  "args": {{}} or null,
  "explanation": "short explanation"
}}
            """)
            try:
                decision = json.loads(decision)
            except Exception:
                decision = {"tool": None, "args": None, "explanation": decision}

            decision["args"] = sanitize_args(decision.get("args", {}))
            explanation = f"💡 ({server}) {decision.get('explanation','')}"
            st.session_state["messages"].append({"role": "assistant", "content": explanation})
            st.chat_message("assistant").markdown(explanation)

            if decision["tool"]:
                st.chat_message("assistant").markdown(
                    f"🔧 Executing *{decision['tool']}* on **{server}** with arguments:\n```json\n{json.dumps(decision['args'], indent=2)}\n```"
                )
                response = call_tool(server, decision["tool"], decision["args"])

                pretty_answer = ask_gemini(
                    f"User asked: {user_input}\n\n"
                    f"Here is the raw {server} response:\n{json.dumps(response, indent=2)}\n\n"
                    f"Answer in natural human-friendly language. "
                    f"If multiple items, format as bullet points."
                )

                st.session_state["messages"].append({"role": "assistant", "content": pretty_answer})
                st.chat_message("assistant").markdown(pretty_answer)
            else:
                answer = ask_gemini(user_input)
                st.session_state["messages"].append({"role": "assistant", "content": answer})
                st.chat_message("assistant").markdown(answer)

if __name__ == "__main__":
    main()
