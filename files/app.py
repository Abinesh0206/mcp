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

# ---------------- TOOL ARGUMENT REQUIREMENTS ----------------
TOOL_ARGUMENTS = {
    "create_application": ["name", "repo_url", "path", "dest_ns", "sync_policy"],
    "get_application": ["application_name"],
    "sync_application": ["application_name"],
    "list_applications": [],
    "get_pods": ["namespace"],
    "get_services": ["namespace"],
    "get_namespaces": [],
}

# ---------------- SERVERS ----------------
def load_servers():
    try:
        with open("servers.json") as f:
            data = json.load(f)
        return data.get("servers", [])
    except Exception:
        return [{
            "name": "default",
            "url": "http://127.0.0.1:3000/mcp",
            "description": "Fallback server"
        }]

servers = load_servers() or [{
    "name": "default",
    "url": "http://127.0.0.1:3000/mcp",
    "description": "Fallback server"
}]

# ---------------- HELPERS ----------------
def call_mcp_server(method: str, params: dict = None, server_url: str = None):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    url = server_url or servers[0]["url"]
    try:
        res = requests.post(
            url,
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        res.raise_for_status()
        return res.json()
    except Exception as e:
        return {"error": str(e)}

def sanitize_args(args: dict):
    if not args:
        return {}
    fixed = args.copy()
    if fixed.get("namespace") == "all":
        fixed["allNamespaces"] = True
        fixed.pop("namespace", None)
    return fixed

def ask_gemini(prompt: str):
    if not GEMINI_AVAILABLE:
        return "Gemini not available."
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(prompt)
        return getattr(response, "text", str(response))
    except Exception as e:
        return f"Gemini error: {str(e)}"

def validate_args(tool: str, args: dict):
    """Ensure required args exist. Returns (valid, missing_fields)."""
    required = TOOL_ARGUMENTS.get(tool, [])
    missing = [arg for arg in required if arg not in (args or {})]
    return (len(missing) == 0, missing)

def ask_gemini_for_tool_decision(query: str):
    # collect server tools
    server_tools = {}
    for s in servers:
        tools_resp = call_mcp_server("tools/list", server_url=s["url"])
        tools = []
        if isinstance(tools_resp, dict):
            result = tools_resp.get("result")
            if isinstance(result, dict):
                tools = result.get("tools", [])
            elif isinstance(result, list):
                tools = result
        server_tools[s["name"]] = [t["name"] for t in tools]

    instruction = f"""
User query: "{query}"

Available servers and tools:
{json.dumps(server_tools, indent=2)}

Pick the BEST match:
Respond in JSON ONLY:
{{
  "server": "<server_name>",
  "tool": "<tool_name>",
  "args": {{}}
}}
"""
    if not GEMINI_AVAILABLE:
        return {"server": None, "tool": None, "args": {}}

    model = genai.GenerativeModel(GEMINI_MODEL)
    response = model.generate_content(instruction)
    text = response.text.strip()

    try:
        parsed = json.loads(text)
    except:
        start, end = text.find("{"), text.rfind("}") + 1
        parsed = json.loads(text[start:end]) if start != -1 else {"server": None, "tool": None, "args": {}}

    parsed["args"] = sanitize_args(parsed.get("args") or {})

    # validate required args
    valid, missing = validate_args(parsed.get("tool"), parsed.get("args"))
    if not valid and missing:
        fix_prompt = f"""
User asked: {query}
You picked tool {parsed['tool']} but args are missing: {missing}.
Re-generate args including ALL required fields.
Respond JSON only.
"""
        fix_resp = model.generate_content(fix_prompt)
        try:
            parsed = json.loads(fix_resp.text.strip())
        except:
            parsed = parsed  # fallback keep old

    return parsed

def check_server_health(server_url: str) -> bool:
    res = call_mcp_server("tools/list", server_url=server_url)
    return isinstance(res, dict) and "result" in res

# ---------------- STREAMLIT APP ----------------
def main():
    st.set_page_config(page_title="MCP Chat Assistant", page_icon="⚡", layout="wide")
    st.title("🤖 Masa Bot Assistant")

    # Sidebar servers
    st.sidebar.subheader("🌐 MCP Servers")
    for s in servers:
        healthy = check_server_health(s["url"])
        icon = "✅" if healthy else "❌"
        st.sidebar.write(f"{s['name']} {icon}")

    # Chat history
    if "messages" not in st.session_state:
        st.session_state["messages"] = []

    for msg in st.session_state["messages"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    with st.form("chat_input", clear_on_submit=True):
        user_input = st.text_input("Ask Kubernetes, ArgoCD, or Jenkins something...")
        submitted = st.form_submit_button("Send")

    if submitted and user_input:
        st.session_state["messages"].append({"role": "user", "content": user_input})
        st.chat_message("user").markdown(user_input)

        decision = ask_gemini_for_tool_decision(user_input)
        if decision.get("tool") and decision.get("server"):
            server = next((s for s in servers if s["name"] == decision["server"]), servers[0])
            st.chat_message("assistant").markdown(
                f"🌐 Using **{decision['server']}** → *{decision['tool']}* with:\n```json\n{json.dumps(decision['args'], indent=2)}\n```"
            )
            response = call_mcp_server("tools/call", {
                "name": decision["tool"], "arguments": decision["args"]
            }, server_url=server["url"])

            pretty = ask_gemini(
                f"User asked: {user_input}\nMCP response:\n{json.dumps(response, indent=2)}\n"
                "Answer in human-friendly format."
            )
            st.session_state["messages"].append({"role": "assistant", "content": pretty})
            st.chat_message("assistant").markdown(pretty)
        else:
            answer = ask_gemini(user_input) if GEMINI_AVAILABLE else "No tool selected."
            st.session_state["messages"].append({"role": "assistant", "content": answer})
            st.chat_message("assistant").markdown(answer)

if __name__ == "__main__":
    main()
