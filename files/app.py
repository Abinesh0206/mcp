# app.py
import os
import json
import requests
import streamlit as st
from dotenv import load_dotenv
import google.generativeai as genai
from datetime import datetime, timezone
from typing import Optional, Dict, Any
import time

# ---------------- CONFIG ----------------
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyApANXlk_-Pc0MrveXl6Umq0KLxdk5wr8c")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

GEMINI_AVAILABLE = False
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        GEMINI_AVAILABLE = True
    except Exception:
        GEMINI_AVAILABLE = False

# ---------------- SERVERS ----------------
def load_servers() -> list:
    try:
        with open("servers.json") as f:
            data = json.load(f)
        return data.get("servers", []) or []
    except Exception:
        return [
            {"name": "kubernetes-mcp", "url": "http://127.0.0.1:3001/mcp"},
            {"name": "argocd-mcp", "url": "http://127.0.0.1:3002/mcp"},
            {"name": "jenkins-mcp", "url": "http://127.0.0.1:3003/mcp"},
        ]

servers = load_servers()
if not servers:
    servers = [
        {"name": "kubernetes-mcp", "url": "http://127.0.0.1:3001/mcp"},
        {"name": "argocd-mcp", "url": "http://127.0.0.1:3002/mcp"},
        {"name": "jenkins-mcp", "url": "http://127.0.0.1:3003/mcp"},
    ]

# ---------------- HELPERS ----------------
def call_mcp_server(method: str, params: Optional[Dict[str, Any]] = None, server_url: Optional[str] = None, timeout: int = 5) -> Dict[str, Any]:
    url = server_url or servers[0]["url"]
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=timeout)
        res.raise_for_status()
        return res.json()
    except Exception:
        return {"error": "unreachable"}

def check_server_health(url: str) -> bool:
    try:
        resp = call_mcp_server("health", server_url=url)
        if resp and not resp.get("error"):
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
    try:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start:end])
    except Exception:
        pass
    return None

# ---------------- GEMINI DECISIONS ----------------
def ask_gemini_for_tool_and_server(query: str, retries: int = 2) -> Dict[str, Any]:
    tool_names = [t.get("name") for s in servers for t in list_mcp_tools(s["url"])]
    server_names = [s["name"] for s in servers]
    instruction = f"""
You are an AI agent that maps a user's query to an MCP tool call and selects the best MCP server.
User query: "{query}"
Available servers: {json.dumps(server_names)}
Available tools: {json.dumps(tool_names)}
Return STRICT JSON only:
{{"tool": "<tool_name_or_null>", "args": {{ ... }}, "server": "<server_name_or_null>", "explanation": "short explanation"}}
If unsure, set tool and server to null.
"""
    if not GEMINI_AVAILABLE:
        return {"tool": None, "args": None, "server": None, "explanation": "Gemini not configured; fallback."}

    for attempt in range(retries):
        try:
            model = genai.GenerativeModel(GEMINI_MODEL)
            resp = model.generate_content(instruction)
            text = getattr(resp, "text", str(resp)).strip()
            parsed = None
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = _extract_json_from_text(text)
            if not isinstance(parsed, dict):
                continue
            parsed["args"] = sanitize_args(parsed.get("args") or {})
            return parsed
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2)
                continue
            return {"tool": None, "args": None, "server": None, "explanation": f"Gemini error: {str(e)}"}

    return {"tool": None, "args": None, "server": None, "explanation": "Gemini failed after retries."}

def ask_gemini_answer(user_input: str, raw_response: dict) -> str:
    if not GEMINI_AVAILABLE:
        return json.dumps(raw_response, indent=2)
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        prompt = (
            f"User asked: {user_input}\n\n"
            f"Raw MCP response:\n{json.dumps(raw_response, indent=2)}\n\n"
            "Convert this into a detailed, human-friendly explanation. "
            "If it's a list, format with bullet points. If it's status, explain health and issues clearly."
        )
        resp = model.generate_content(prompt)
        return getattr(resp, "text", str(resp)).strip()
    except Exception as e:
        return f"Gemini error while post-processing: {str(e)}"

# ---------------- STREAMLIT APP ----------------
def main():
    st.set_page_config(page_title="MCP Chat Assistant", page_icon="⚡", layout="wide")
    st.title("🤖 Masa Bot Assistant")

    # ---- Sidebar with Server Status ----
    st.sidebar.title("🌐 MCP Servers")
    for s in servers:
        status = "✅" if check_server_health(s["url"]) else "❌"
        st.sidebar.write(f"{s['name']} {status}")

    if "messages" not in st.session_state:
        st.session_state["messages"] = []

    # Render chat history
    for msg in st.session_state["messages"]:
        role = msg.get("role", "assistant")
        with st.chat_message(role):
            st.markdown(msg.get("content", ""))

    # Chat input
    user_prompt = st.chat_input("Ask Kubernetes or ArgoCD something...")
    if user_prompt:
        st.session_state["messages"].append({"role": "user", "content": user_prompt})
        st.chat_message("user").markdown(user_prompt)

        decision = ask_gemini_for_tool_and_server(user_prompt)
        explanation = f"💡 {decision.get('explanation', '')}" if decision.get("explanation") else "💡 Tool decision produced."
        st.session_state["messages"].append({"role": "assistant", "content": explanation})
        st.chat_message("assistant").markdown(explanation)

        server_name = decision.get("server")
        server_url = None
        if server_name:
            for s in servers:
                if s["name"] == server_name:
                    server_url = s["url"]
                    break
        if not server_url:
            server_url = servers[0]["url"]

        tool_name = decision.get("tool")
        if tool_name:
            tool_args = decision.get("args") or {}
            st.chat_message("assistant").markdown(
                f"🔧 Executing *{tool_name}* on server `{server_name}` with arguments:\n```json\n{json.dumps(tool_args, indent=2)}\n```"
            )
            resp = call_tool(tool_name, tool_args, server_url=server_url)

            if not resp or "error" in resp:
                final_answer = f"⚠️ No valid response received. {resp.get('error', 'Unknown error') if isinstance(resp, dict) else ''}"
            else:
                final_answer = ask_gemini_answer(user_prompt, resp)

            st.session_state["messages"].append({"role": "assistant", "content": final_answer})
            st.chat_message("assistant").markdown(final_answer)
        else:
            answer = "⚠️ No tool selected. Try again or check available MCP tools."
            st.session_state["messages"].append({"role": "assistant", "content": answer})
            st.chat_message("assistant").markdown(answer)

if __name__ == "__main__":
    main()
