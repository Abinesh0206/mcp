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

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyBYRBa7dQ5atjlHk7e3IOdZBdo6OOcn2Pk")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

# Configure Gemini SDK if key present
GEMINI_AVAILABLE = False
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        GEMINI_AVAILABLE = True
    except Exception:
        GEMINI_AVAILABLE = False

# ---------------- SERVERS ----------------
def load_servers():
    """Load server list from servers.json, fallback to default."""
    try:
        with open("servers.json") as f:
            data = json.load(f)
        return data.get("servers", []) or []
    except Exception:
        # fallback minimal server
        return [{
            "name": "default",
            "url": "http://127.0.0.1:3000/mcp",
            "description": "Fallback server"
        }]

servers = load_servers()
if not servers:
    servers = [{"name": "default", "url": "http://127.0.0.1:3000/mcp", "description": "Fallback server"}]

# Initialize current server in session state
if "current_server" not in st.session_state:
    st.session_state["current_server"] = servers[0]["url"]


def get_current_server_url():
    return st.session_state.get("current_server", servers[0]["url"])


# ---------------- HELPERS ----------------
def call_mcp_server(method: str, params: dict = None):
    """Call MCP server with JSON-RPC payload and return parsed JSON or error dict."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or {}
    }
    try:
        res = requests.post(
            get_current_server_url(),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json=payload,
            timeout=30,
        )
        res.raise_for_status()
        text = res.text.strip()
        # handle SSE-ish response that contains lines with `data: {...}`
        if text.startswith("event:") or "data:" in text:
            for line in text.splitlines():
                if line.startswith("data:"):
                    payload_text = line[len("data:"):].strip()
                    try:
                        return json.loads(payload_text)
                    except Exception:
                        # fallback to raw text
                        return {"result": payload_text}
        # normal JSON
        try:
            return res.json()
        except ValueError:
            return {"result": res.text}
    except requests.exceptions.RequestException as e:
        return {"error": f"MCP server request failed: {str(e)}"}


def list_mcp_tools():
    """Fetch available MCP tools and return list of tool dicts."""
    resp = call_mcp_server("tools/list")
    if not isinstance(resp, dict):
        return []
    # Some MCP servers return {"result": {"tools":[...]}} or {"result": [...]}
    result = resp.get("result")
    if isinstance(result, dict):
        return result.get("tools", [])
    if isinstance(result, list):
        return result
    return []


def call_tool(name: str, arguments: dict):
    """Execute MCP tool by name with arguments. Returns parsed response dict."""
    if not name or not isinstance(arguments, dict):
        return {"error": "Invalid tool name or arguments"}
    return call_mcp_server("tools/call", {"name": name, "arguments": arguments})


def humanize_age(created_at: str) -> str:
    """Convert ISO datetime to human-readable relative age."""
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
            return f"{hours}h{minutes % 60}m"
        days = hours // 24
        hours = hours % 24
        return f"{days}d{hours}h"
    except Exception:
        return "-"


def ask_gemini(prompt: str):
    """Ask Gemini for free-text natural language generation (if available)."""
    if not GEMINI_AVAILABLE:
        return "Gemini not configured or unavailable."
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(prompt)
        return response.text if hasattr(response, "text") else str(response)
    except Exception as e:
        return f"Gemini error: {str(e)}"


def sanitize_args(args: dict):
    """Fix arguments before sending to MCP tools."""
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
    """Use Gemini to map user query -> MCP tool + arguments. If Gemini not available, return null decision."""
    tools = list_mcp_tools()
    tool_names = [t["name"] for t in tools]

    instruction = f"""
You are an AI agent that maps user queries to MCP tools.
User query: "{query}"

Available tools in this MCP server: {json.dumps(tool_names, indent=2)}

Rules:
- Only choose from the tools above.
- If the query clearly maps to a tool, return tool + args in JSON.
- If unsure, set tool=null and args=null.

Respond ONLY in strict JSON:
{{"tool": "<tool_name>" | null, "args": {{}} | null, "explanation": "Short explanation"}}
"""
    if not GEMINI_AVAILABLE:
        return {"tool": None, "args": None, "explanation": "Gemini not configured; fallback to chat reply."}
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(instruction)
        text = response.text.strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start != -1 and end != -1:
                parsed = json.loads(text[start:end])
            else:
                parsed = {"tool": None, "args": None, "explanation": f"Gemini invalid response: {text}"}
        parsed["args"] = sanitize_args(parsed.get("args") or {})
        return parsed
    except Exception as e:
        return {"tool": None, "args": None, "explanation": f"Gemini error: {str(e)}"}


# ---------------- STREAMLIT APP ----------------
def main():
    st.set_page_config(page_title="MCP Chat Assistant", page_icon="⚡", layout="wide")
    st.title("🤖 Masa Bot Assistant")

    # Sidebar: select MCP server
    st.sidebar.subheader("🌐 Select MCP Server")
    server_options = [f"{s['name']} — {s['url']}" for s in servers]
    choice = st.sidebar.radio("Available Servers:", server_options)
    selected = next((s for s in servers if choice.startswith(s["name"])), servers[0])
    st.session_state["current_server"] = selected["url"]

    # NOTE: Sidebar button removed intentionally.
    # The form will open only when the user types "create application" in the chat input.

    # Show tools for current server in sidebar
    tools = list_mcp_tools()
    st.sidebar.subheader("🔧 Available MCP Tools")
    if tools:
        for t in tools:
            st.sidebar.write(f"- **{t.get('name','?')}**: {t.get('description','')}")
    else:
        st.sidebar.error("⚠ Could not fetch tools from MCP server.")

    # Initialize chat history
    if "messages" not in st.session_state:
        st.session_state["messages"] = []

    # Init create application form flow
    if "create_flow_form" not in st.session_state:
        st.session_state["create_flow_form"] = False

    # Display chat history (main column)
    for msg in st.session_state["messages"]:
        role = msg.get("role", "assistant")
        with st.chat_message(role):
            st.markdown(msg.get("content", ""))

    # Chat input area
    with st.form("user_input_form", clear_on_submit=True):
        user_input = st.text_input("Ask Kubernetes or ArgoCD something...")
        submitted = st.form_submit_button("Send")

    if submitted and user_input:
        # If user typed "create application" open the form-mode flow
        if user_input.lower().strip() == "create application" and not st.session_state["create_flow_form"]:
            st.session_state["create_flow_form"] = True
            prompt = "Opening Create ArgoCD Application form..."
            st.session_state["messages"].append({"role": "assistant", "content": prompt})
            st.chat_message("assistant").markdown(prompt)
            return

        # Normal flow: add user message then use Gemini tool-decider (if available)
        st.session_state["messages"].append({"role": "user", "content": user_input})
        st.chat_message("user").markdown(user_input)

        decision = ask_gemini_for_tool_decision(user_input)
        explanation = f"💡 {decision.get('explanation', '')}"
        st.session_state["messages"].append({"role": "assistant", "content": explanation})
        st.chat_message("assistant").markdown(explanation)

        if decision.get("tool"):
            st.chat_message("assistant").markdown(
                f"🔧 Executing *{decision['tool']}* with arguments:\n```json\n{json.dumps(decision['args'], indent=2)}\n```"
            )
            response = call_tool(decision["tool"], decision["args"] or {})

            if GEMINI_AVAILABLE:
                pretty_answer = ask_gemini(
                    f"User asked: {user_input}\n\nHere is the raw MCP response:\n{json.dumps(response, indent=2)}\n\n"
                    f"Answer in natural human-friendly language. If multiple items (pods, apps, projects, services), format as bullet points."
                )
                st.session_state["messages"].append({"role": "assistant", "content": pretty_answer})
                st.chat_message("assistant").markdown(pretty_answer)
            else:
                fallback = json.dumps(response, indent=2)
                st.session_state["messages"].append({"role": "assistant", "content": fallback})
                st.chat_message("assistant").markdown(fallback)
        else:
            # No tool decided: fallback to plain Gemini chat or echo fallback
            if GEMINI_AVAILABLE:
                answer = ask_gemini(user_input)
            else:
                answer = "No tool selected and Gemini not available. Please use a direct command or the Create Application form."
            st.session_state["messages"].append({"role": "assistant", "content": answer})
            st.chat_message("assistant").markdown(answer)


if __name__ == "__main__":
    main()
