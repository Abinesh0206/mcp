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

def set_current_server_by_query(query: str):
    """Auto-route to correct MCP server based on query text."""
    q = query.lower()
    if "k8s" in q or "kubernetes" in q or "pod" in q or "namespace" in q:
        target = next((s for s in servers if "kubernetes" in s["name"].lower()), servers[0])
    elif "argo" in q or "argocd" in q or "application" in q:
        target = next((s for s in servers if "argo" in s["name"].lower()), servers[0])
    elif "jenkins" in q or "pipeline" in q or "build" in q:
        target = next((s for s in servers if "jenkins" in s["name"].lower()), servers[0])
    else:
        target = servers[0]  # fallback
    st.session_state["current_server"] = target["url"]

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
        if text.startswith("event:") or "data:" in text:
            for line in text.splitlines():
                if line.startswith("data:"):
                    payload_text = line[len("data:"):].strip()
                    try:
                        return json.loads(payload_text)
                    except Exception:
                        return {"result": payload_text}
        try:
            return res.json()
        except ValueError:
            return {"result": res.text}
    except requests.exceptions.RequestException as e:
        return {"error": f"MCP server request failed: {str(e)}"}

def list_mcp_tools():
    resp = call_mcp_server("tools/list")
    if not isinstance(resp, dict):
        return []
    result = resp.get("result")
    if isinstance(result, dict):
        return result.get("tools", [])
    if isinstance(result, list):
        return result
    return []

def call_tool(name: str, arguments: dict):
    return call_mcp_server("tools/call", {"name": name, "arguments": arguments})

def ask_gemini(prompt: str):
    if not GEMINI_AVAILABLE:
        return "Gemini not configured or unavailable."
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(prompt)
        return response.text if hasattr(response, "text") else str(response)
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

def ask_gemini_for_tool_decision(query: str):
    tools = list_mcp_tools()
    tool_names = [t["name"] for t in tools]

    instruction = f"""
You are an AI agent that maps user queries to MCP tools.
User query: "{query}"

Available tools: {json.dumps(tool_names, indent=2)}

Rules:
- Choose only from tools above.
- Respond ONLY JSON.

Format:
{{"tool": "<tool_name>" | null, "args": {{}} | null}}
"""
    if not GEMINI_AVAILABLE:
        return {"tool": None, "args": None}
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(instruction)
        text = response.text.strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = {"tool": None, "args": None}
        parsed["args"] = sanitize_args(parsed.get("args") or {})
        return parsed
    except Exception:
        return {"tool": None, "args": None}

# ---------------- STREAMLIT APP ----------------
def main():
    st.set_page_config(page_title="MCP Chat Assistant", page_icon="⚡", layout="wide")
    st.title("🤖 Masa Bot Assistant")

    if "messages" not in st.session_state:
        st.session_state["messages"] = []

    for msg in st.session_state["messages"]:
        with st.chat_message(msg.get("role", "assistant")):
            st.markdown(msg.get("content", ""))

    with st.form("user_input_form", clear_on_submit=True):
        user_input = st.text_input("Ask me something about Kubernetes, ArgoCD, or Jenkins...")
        submitted = st.form_submit_button("Send")

    if submitted and user_input:
        st.session_state["messages"].append({"role": "user", "content": user_input})
        st.chat_message("user").markdown(user_input)

        # Auto route to server
        set_current_server_by_query(user_input)

        # Pick tool + args
        decision = ask_gemini_for_tool_decision(user_input)

        if decision.get("tool"):
            response = call_tool(decision["tool"], decision["args"] or {})
            if GEMINI_AVAILABLE:
                pretty_answer = ask_gemini(
                    f"User asked: {user_input}\n\nHere is the raw MCP response:\n{json.dumps(response, indent=2)}\n\n"
                    f"Answer in natural human-friendly language. Do NOT show JSON. Use clean text or bullet points only."
                )
                st.session_state["messages"].append({"role": "assistant", "content": pretty_answer})
                st.chat_message("assistant").markdown(pretty_answer)
            else:
                fallback = json.dumps(response, indent=2)
                st.session_state["messages"].append({"role": "assistant", "content": fallback})
                st.chat_message("assistant").markdown(fallback)
        else:
            answer = ask_gemini(user_input) if GEMINI_AVAILABLE else "No tool found."
            st.session_state["messages"].append({"role": "assistant", "content": answer})
            st.chat_message("assistant").markdown(answer)

if __name__ == "__main__":
    main()
