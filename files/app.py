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

# ---------------- HELPERS ----------------
def call_mcp_server(method: str, params: dict = None, server_url: str = None):
    """Call MCP server with JSON-RPC payload and return parsed JSON or error dict."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or {}
    }
    url = server_url or servers[0]["url"]
    try:
        res = requests.post(
            url,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json=payload,
            timeout=30,
        )
        res.raise_for_status()
        try:
            return res.json()
        except ValueError:
            return {"result": res.text}
    except requests.exceptions.RequestException as e:
        return {"error": f"MCP server request failed: {str(e)}"}


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


def ask_gemini_for_tool_decision(query: str):
    """Use Gemini to decide MCP server + tool + arguments."""
    # Build list of servers + tools
    server_tools = {}
    for s in servers:
        tool_list = call_mcp_server("tools/list", server_url=s["url"])
        tools = []
        if isinstance(tool_list, dict):
            result = tool_list.get("result")
            if isinstance(result, dict):
                tools = result.get("tools", [])
            elif isinstance(result, list):
                tools = result
        server_tools[s["name"]] = [t["name"] for t in tools]

    instruction = f"""
User query: "{query}"

Available MCP servers and tools:
{json.dumps(server_tools, indent=2)}

Choose the BEST server and one tool + args.

Respond in strict JSON:
{{
  "server": "<server_name>" | null,
  "tool": "<tool_name>" | null,
  "args": {{}} | null,
  "explanation": "short explanation"
}}
"""

    if not GEMINI_AVAILABLE:
        return {"server": None, "tool": None, "args": None, "explanation": "Gemini not available"}

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
                parsed = {"server": None, "tool": None, "args": None, "explanation": f"Gemini invalid response: {text}"}
        parsed["args"] = sanitize_args(parsed.get("args") or {})
        return parsed
    except Exception as e:
        return {"server": None, "tool": None, "args": None, "explanation": f"Gemini error: {str(e)}"}


# ---------------- STREAMLIT APP ----------------
def main():
    st.set_page_config(page_title="MCP Chat Assistant", page_icon="⚡", layout="wide")
    st.title("🤖 Masa Bot Assistant")

    # Sidebar: show servers
    st.sidebar.subheader("🌐 MCP Servers")
    for s in servers:
        st.sidebar.write(f"- **{s['name']}** — {s['url']}")

    # Initialize chat history
    if "messages" not in st.session_state:
        st.session_state["messages"] = []

    # Display chat history
    for msg in st.session_state["messages"]:
        role = msg.get("role", "assistant")
        with st.chat_message(role):
            st.markdown(msg.get("content", ""))

    # Chat input area
    with st.form("user_input_form", clear_on_submit=True):
        user_input = st.text_input("Ask Kubernetes, ArgoCD, or Jenkins something...")
        submitted = st.form_submit_button("Send")

    if submitted and user_input:
        st.session_state["messages"].append({"role": "user", "content": user_input})
        st.chat_message("user").markdown(user_input)

        # Decide server + tool + args
        decision = ask_gemini_for_tool_decision(user_input)
        explanation = f"💡 {decision.get('explanation', '')}"
        st.session_state["messages"].append({"role": "assistant", "content": explanation})
        st.chat_message("assistant").markdown(explanation)

        if decision.get("tool") and decision.get("server"):
            # Find server URL
            server = next((s for s in servers if s["name"] == decision["server"]), servers[0])
            server_url = server["url"]

            st.chat_message("assistant").markdown(
                f"🌐 Using **{decision['server']}** → Executing *{decision['tool']}* with args:\n```json\n{json.dumps(decision['args'], indent=2)}\n```"
            )
            response = call_mcp_server("tools/call", {"name": decision["tool"], "arguments": decision["args"]}, server_url=server_url)

            if GEMINI_AVAILABLE:
                pretty_answer = ask_gemini(
                    f"User asked: {user_input}\n\nHere is the raw MCP response:\n{json.dumps(response, indent=2)}\n\n"
                    f"Answer in natural human-friendly language. If multiple items, format as bullet points."
                )
                st.session_state["messages"].append({"role": "assistant", "content": pretty_answer})
                st.chat_message("assistant").markdown(pretty_answer)
            else:
                fallback = json.dumps(response, indent=2)
                st.session_state["messages"].append({"role": "assistant", "content": fallback})
                st.chat_message("assistant").markdown(fallback)
        else:
            # No tool decided
            if GEMINI_AVAILABLE:
                answer = ask_gemini(user_input)
            else:
                answer = "No tool selected and Gemini not available."
            st.session_state["messages"].append({"role": "assistant", "content": answer})
            st.chat_message("assistant").markdown(answer)


if __name__ == "__main__":
    main()
