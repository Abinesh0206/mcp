# app.py
import os
import json
import requests
import streamlit as st
from dotenv import load_dotenv
import google.generativeai as genai
from datetime import datetime, timezone
from typing import Optional, Dict, Any

# ---------------- CONFIG ----------------
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyBlx9uMCC18Uaw4LdhmXmQxsYlpf2DBONo")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

# Configure Gemini SDK if available
GEMINI_AVAILABLE = False
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        GEMINI_AVAILABLE = True
    except Exception:
        GEMINI_AVAILABLE = False

# ---------------- SERVERS ----------------
def load_servers() -> list:
    """Load server list from servers.json, fallback to single default server."""
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

# Ensure current_server in session state
if "current_server" not in st.session_state:
    st.session_state["current_server"] = servers[0]["url"]

def get_current_server_url() -> str:
    return st.session_state.get("current_server", servers[0]["url"])

# ---------------- HELPERS ----------------
def call_mcp_server(method: str, params: Optional[Dict[str, Any]] = None, server_url: Optional[str] = None, timeout: int = 20) -> Dict[str, Any]:
    """Call MCP server with JSON-RPC and return parsed result or error dict.
       Handles both normal JSON and simple SSE-like responses containing lines 'data: {...}'."""
    url = server_url or get_current_server_url()
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream, */*",
    }
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=timeout)
        res.raise_for_status()
        text = res.text or ""
        text = text.strip()
        # SSE-ish: search for first "data:" JSON content
        if text.startswith("event:") or "data:" in text:
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("data:"):
                    payload_text = line[len("data:"):].strip()
                    try:
                        return json.loads(payload_text)
                    except Exception:
                        return {"result": payload_text}
        # Try normal JSON
        try:
            return res.json()
        except ValueError:
            # not JSON, return raw text under result
            return {"result": res.text}
    except requests.exceptions.RequestException as e:
        return {"error": f"MCP server request failed: {str(e)}"}

def check_server_health(server_url: str) -> bool:
    """Try 'health' method first; fallback to 'tools/list' to infer availability."""
    try:
        resp = call_mcp_server("health", server_url=server_url, timeout=6)
        if isinstance(resp, dict) and ("result" in resp or "status" in resp):
            return True
    except Exception:
        pass
    # fallback
    try:
        resp = call_mcp_server("tools/list", server_url=server_url, timeout=6)
        if isinstance(resp, dict) and "result" in resp:
            return True
    except Exception:
        pass
    return False

def list_mcp_tools(server_url: Optional[str] = None) -> list:
    resp = call_mcp_server("tools/list", server_url=server_url)
    if not isinstance(resp, dict):
        return []
    result = resp.get("result")
    if isinstance(result, dict):
        return result.get("tools", []) or []
    if isinstance(result, list):
        return result
    return []

def call_tool(name: str, arguments: dict, server_url: Optional[str] = None) -> Dict[str, Any]:
    return call_mcp_server("tools/call", {"name": name, "arguments": arguments or {}}, server_url=server_url)

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
            return f"{hours}h{minutes % 60}m"
        days = hours // 24
        hours = hours % 24
        return f"{days}d{hours}h"
    except Exception:
        return "-"

def sanitize_args(args: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not args:
        return {}
    fixed = dict(args)
    if "resource" in fixed and "resourceType" not in fixed:
        fixed["resourceType"] = fixed.pop("resource")
    if fixed.get("resourceType") == "pods" and "namespace" not in fixed:
        fixed["namespace"] = "default"
    if fixed.get("namespace") == "all":
        fixed["allNamespaces"] = True
        fixed.pop("namespace", None)
    return fixed

def _extract_json_from_text(text: str) -> Optional[dict]:
    """Try to extract a JSON object substring from a free text response."""
    try:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start:end])
    except Exception:
        pass
    return None

def ask_gemini_for_tool_decision(query: str) -> Dict[str, Any]:
    """Ask Gemini to map natural query -> {'tool':name or None, 'args':{}}"""
    tools = list_mcp_tools()
    tool_names = [t.get("name") for t in tools]
    instruction = f"""
You are an AI agent that maps a user's short query to an MCP tool call.
User query: "{query}"
Available tools: {json.dumps(tool_names)}
Return STRICT JSON only:
{{"tool": "<tool_name_or_null>", "args": {{ ... }} , "explanation": "short explanation"}}
If unsure, set tool to null and args to null.
"""
    if not GEMINI_AVAILABLE:
        return {"tool": None, "args": None, "explanation": "Gemini not configured; fallback."}
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        resp = model.generate_content(instruction)
        text = getattr(resp, "text", str(resp)).strip()
        parsed = None
        # try direct parse
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = _extract_json_from_text(text)
        if not isinstance(parsed, dict):
            return {"tool": None, "args": None, "explanation": f"Gemini response couldn't be parsed: {text[:200]}"}
        parsed["args"] = sanitize_args(parsed.get("args") or {})
        return parsed
    except Exception as e:
        return {"tool": None, "args": None, "explanation": f"Gemini error: {str(e)}"}

def ask_gemini_answer(user_input: str, raw_response: dict) -> str:
    """Ask Gemini to convert raw MCP response to friendly text (if available)."""
    if not GEMINI_AVAILABLE:
        return json.dumps(raw_response, indent=2)
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        prompt = (
            f"User asked: {user_input}\n\n"
            f"Raw MCP response:\n{json.dumps(raw_response, indent=2)}\n\n"
            "Convert to concise, human-friendly answer. Use bullets if multiple items."
        )
        resp = model.generate_content(prompt)
        return getattr(resp, "text", str(resp)).strip()
    except Exception as e:
        return f"Gemini error while post-processing: {str(e)}"

# ---------------- STREAMLIT APP ----------------
def main():
    st.set_page_config(page_title="MCP Chat Assistant", page_icon="⚡", layout="wide")
    st.title("🤖 Masa Bot Assistant")

    # Sidebar: server statuses + selection
    st.sidebar.subheader("🌐 MCP Servers")
    for s in servers:
        status_icon = "✅" if check_server_health(s["url"]) else "❌"
        st.sidebar.markdown(f"**{s['name']}** — {s['url']} {status_icon}")
    st.sidebar.markdown("---")

    server_options = [f"{s['name']} — {s['url']}" for s in servers]
    choice = st.sidebar.radio("Active Server:", server_options)
    selected = next((s for s in servers if choice.startswith(s["name"])), servers[0])
    st.session_state["current_server"] = selected["url"]

    # Sidebar: tools
    st.sidebar.subheader("🔧 Available MCP Tools")
    tools = list_mcp_tools()
    if tools:
        for t in tools:
            name = t.get("name", "?")
            desc = t.get("description", "")
            st.sidebar.markdown(f"- **{name}** — {desc}")
    else:
        st.sidebar.info("No tools available or couldn't fetch tools from server.")

    # Initialize chat history
    if "messages" not in st.session_state:
        st.session_state["messages"] = []
    if "create_flow_form" not in st.session_state:
        st.session_state["create_flow_form"] = False

    # Render chat history
    for msg in st.session_state["messages"]:
        role = msg.get("role", "assistant")
        with st.chat_message(role):
            st.markdown(msg.get("content", ""))

    # If create application flow is active, show the form
    if st.session_state["create_flow_form"]:
        st.markdown("### Create ArgoCD Application — Form")
        with st.form("create_app_form"):
            app_name = st.text_input("Application Name", value="")
            project = st.text_input("Project", value="default")
            repo_url = st.text_input("Repository URL", value="")
            path = st.text_input("Path (in repo)", value="")
            dest_ns = st.text_input("Destination Namespace", value="default")
            submit_create = st.form_submit_button("Create Application")
            cancel_create = st.form_submit_button("Cancel")
        if cancel_create:
            st.session_state["create_flow_form"] = False
            st.session_state["messages"].append({"role": "assistant", "content": "Create application flow cancelled."})
            st.experimental_rerun()
        if submit_create:
            # Build args object
            args = {
                "name": app_name,
                "project": project,
                "repo_url": repo_url,
                "path": path,
                "dest_ns": dest_ns
            }
            # Try to find a likely tool to call (heuristic)
            candidate = None
            for t in tools:
                n = t.get("name", "").lower()
                if "create" in n and "app" in n or "application" in n:
                    candidate = t.get("name")
                    break
            st.session_state["create_flow_form"] = False
            st.session_state["messages"].append({"role": "assistant", "content": f"Submitting Create Application: ```json\n{json.dumps(args, indent=2)}\n```"})
            if candidate:
                resp = call_tool(candidate, args)
                pretty = ask_gemini_answer("Create ArgoCD Application", resp)
                st.session_state["messages"].append({"role": "assistant", "content": pretty})
            else:
                st.session_state["messages"].append({"role": "assistant", "content": "No create-application tool found on this MCP server; showing JSON payload instead."})
            st.experimental_rerun()

    # Chat input area (use chat_input if available)
    user_prompt = st.chat_input("Ask Kubernetes or ArgoCD something...")
    if user_prompt:
        st.session_state["messages"].append({"role": "user", "content": user_prompt})
        st.chat_message("user").markdown(user_prompt)

        # special-case: open create app form
        if user_prompt.strip().lower() == "create application" and not st.session_state["create_flow_form"]:
            st.session_state["create_flow_form"] = True
            prompt = "Opening Create ArgoCD Application form..."
            st.session_state["messages"].append({"role": "assistant", "content": prompt})
            st.chat_message("assistant").markdown(prompt)
            st.experimental_rerun()

        # Ask Gemini to pick a tool (best-effort)
        decision = ask_gemini_for_tool_decision(user_prompt)
        explanation = f"💡 {decision.get('explanation', '')}" if decision.get("explanation") else "💡 Tool decision produced."
        st.session_state["messages"].append({"role": "assistant", "content": explanation})
        st.chat_message("assistant").markdown(explanation)

        # If Gemini selected a tool, call it
        if decision.get("tool"):
            tool_name = decision["tool"]
            tool_args = decision.get("args") or {}
            st.chat_message("assistant").markdown(f"🔧 Executing *{tool_name}* with arguments:\n```json\n{json.dumps(tool_args, indent=2)}\n```")
            resp = call_tool(tool_name, tool_args)
            final_answer = ask_gemini_answer(user_prompt, resp)
            st.session_state["messages"].append({"role": "assistant", "content": final_answer})
            st.chat_message("assistant").markdown(final_answer)
        else:
            # No tool chosen: if Gemini available, fallback to general chat; else give guidance
            if GEMINI_AVAILABLE:
                answer = ask_gemini_answer(user_prompt, {"note": "No tool selected; performing chat fallback."})
            else:
                answer = "No tool selected and Gemini is not configured. Try a direct command like a tool name or type 'create application'."
            st.session_state["messages"].append({"role": "assistant", "content": answer})
            st.chat_message("assistant").markdown(answer)

if __name__ == "__main__":
    main()
