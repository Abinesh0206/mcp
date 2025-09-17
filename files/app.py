# app.py
import os
import json
import requests
import streamlit as st
from dotenv import load_dotenv
import google.generativeai as genai
from datetime import datetime, timezone

# ---------------- CONFIG ----------------
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyBlx9uMCC18Uaw4LdhmXmQxsYlpf2DBONo")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

# Configure Gemini SDK
GEMINI_AVAILABLE = False
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        GEMINI_AVAILABLE = True
    except Exception:
        GEMINI_AVAILABLE = False


# ---------------- SERVERS ----------------
def load_servers():
    """Load servers.json or fallback to default"""
    try:
        with open("servers.json") as f:
            data = json.load(f)
        return data.get("servers", []) or []
    except Exception:
        return [{
            "name": "default",
            "url": "http://127.0.0.1:3000/mcp",
            "description": "Fallback server"
        }]

servers = load_servers()
if not servers:
    servers = [{"name": "default", "url": "http://127.0.0.1:3000/mcp", "description": "Fallback server"}]

if "current_server" not in st.session_state:
    st.session_state["current_server"] = servers[0]["url"]


def get_current_server_url():
    return st.session_state.get("current_server", servers[0]["url"])


# ---------------- HELPERS ----------------
def call_mcp_server(method: str, params: dict = None, server_url: str = None):
    """Send JSON-RPC to MCP server and parse response"""
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    url = server_url or get_current_server_url()
    try:
        res = requests.post(
            url,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            json=payload,
            timeout=15,
        )
        res.raise_for_status()
        return res.json()
    except Exception as e:
        return {"error": str(e)}


def check_server_health(server_url: str):
    """Check server health: try 'health' first, fallback to 'tools/list'"""
    # Try health method first
    resp = call_mcp_server("health", server_url=server_url)
    if isinstance(resp, dict) and "result" in resp:
        return True
    # Fallback to tools/list
    resp = call_mcp_server("tools/list", server_url=server_url)
    return isinstance(resp, dict) and "result" in resp


def list_mcp_tools():
    resp = call_mcp_server("tools/list")
    result = resp.get("result")
    if isinstance(result, dict):
        return result.get("tools", [])
    if isinstance(result, list):
        return result
    return []


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


def ask_gemini_for_tool_decision(query: str):
    """Gemini decides tool + args"""
    tools = list_mcp_tools()
    tool_names = [t["name"] for t in tools]

    instruction = f"""
User query: "{query}"
Available tools: {json.dumps(tool_names, indent=2)}

Respond ONLY JSON:
{{"tool": "<tool_name>" | null, "args": {{}} | null}}
"""

    if not GEMINI_AVAILABLE:
        return {"tool": None, "args": None}

    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(instruction)
        text = response.text.strip()
        parsed = json.loads(text[text.find("{"):text.rfind("}")+1])
        parsed["args"] = sanitize_args(parsed.get("args") or {})
        return parsed
    except Exception:
        return {"tool": None, "args": None}


def ask_gemini_answer(user_input: str, raw_response: dict):
    if not GEMINI_AVAILABLE:
        return json.dumps(raw_response, indent=2)
    model = genai.GenerativeModel(GEMINI_MODEL)
    response = model.generate_content(
        f"User asked: {user_input}\n\nRaw response:\n{json.dumps(raw_response, indent=2)}\n\n"
        "Convert to clean English answer with bullet points if needed."
    )
    return response.text.strip()


# ---------------- STREAMLIT APP ----------------
def main():
    st.set_page_config(page_title="MCP Chat Assistant", page_icon="⚡", layout="wide")
    st.title("🤖 Masa Bot Assistant")

    # Sidebar: server statuses
    st.sidebar.subheader("🌐 MCP Servers")
    for s in servers:
        status_icon = "✅" if check_server_health(s["url"]) else "❌"
        st.sidebar.markdown(f"{s['name']} {status_icon}")

    # Sidebar: select server
    server_options = [f"{s['name']} — {s['url']}" for s in servers]
    choice = st.sidebar.radio("Active Server:", server_options)
    selected = next((s for s in servers if choice.startswith(s["name"])), servers[0])
    st.session_state["current_server"] = selected["url"]

    # Sidebar: tools
    st.sidebar.subheader("🔧 Tools")
    for t in list_mcp_tools():
        st.sidebar.write(f"- {t.get('name')}")

    # Chat history
    if "messages" not in st.session_state:
        st.session_state["messages"] = []

    for msg in st.session_state["messages"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Chat input
    if prompt := st.chat_input("Ask something..."):
        st.session_state["messages"].append({"role": "user", "content": prompt})
        st.chat_message("user").markdown(prompt)

        decision = ask_gemini_for_tool_decision(prompt)
        if decision.get("tool"):
            response = call_tool(decision["tool"], decision["args"])
            final_answer = ask_gemini_answer(prompt, response)
        else:
            final_answer = "No tool available." if not GEMINI_AVAILABLE else "Gemini couldn't pick a tool."

        st.session_state["messages"].append({"role": "assistant", "content": final_answer})
        st.chat_message("assistant").markdown(final_answer)


if __name__ == "__main__":
    main()
