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

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyCeUhwJf1-qRz2wy3y680JNXmpcG6LkfhQ")
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
        return [
            {"name": "kubernetes-mcp", "url": "http://127.0.0.1:3001/mcp", "description": "Kubernetes MCP"},
            {"name": "argocd-mcp", "url": "http://127.0.0.1:3002/mcp", "description": "ArgoCD MCP"},
            {"name": "jenkins-mcp", "url": "http://127.0.0.1:3003/mcp", "description": "Jenkins MCP"},
        ]

servers = load_servers()
server_map = {s["name"]: s["url"] for s in servers}

if "current_server" not in st.session_state:
    st.session_state["current_server"] = servers[0]["url"]


def get_current_server_url():
    return st.session_state.get("current_server", servers[0]["url"])


# ---------------- HELPERS ----------------
def call_mcp_server(method: str, params: dict = None):
    """Call MCP server with JSON-RPC payload and return parsed JSON or error dict."""
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
    """Fetch available MCP tools and return list of tool dicts."""
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
    """Execute MCP tool by name with arguments. Returns parsed response dict."""
    if not name or not isinstance(arguments, dict):
        return {"error": "Invalid tool name or arguments"}
    return call_mcp_server("tools/call", {"name": name, "arguments": arguments})


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


# ---------------- GEMINI DECISION HELPERS ----------------
def ask_gemini_for_server_and_tool(query: str):
    """Decide which MCP server + tool + args to use."""
    instruction = f"""
You are an AI router. Route user queries to the correct MCP server and tool.

User query: "{query}"

Available servers:
- kubernetes-mcp → for Kubernetes cluster operations (pods, nodes, services, resources)
- argocd-mcp → for ArgoCD operations (applications, projects, sync, deployments)
- jenkins-mcp → for Jenkins operations (jobs, builds, plugins, pipelines)

Rules:
1. First decide the correct server.
2. Then choose the right tool from that server.
3. Provide args needed.
4. If unsure, set server=null and tool=null.

Respond in strict JSON only:
{{
  "server": "kubernetes-mcp" | "argocd-mcp" | "jenkins-mcp" | null,
  "tool": "<tool_name>" | null,
  "args": {{}} | null,
  "explanation": "short reasoning"
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
            parsed = json.loads(text[start:end]) if start != -1 and end != -1 else {}
        parsed["args"] = sanitize_args(parsed.get("args") or {})
        return parsed
    except Exception as e:
        return {"server": None, "tool": None, "args": None, "explanation": f"Gemini error: {str(e)}"}


# ---------------- STREAMLIT APP ----------------
def main():
    st.set_page_config(page_title="MCP Chat Assistant", page_icon="⚡", layout="wide")
    st.title("🤖 Masa Bot Assistant")

    # Show servers (read-only info, no selection)
    st.sidebar.subheader("🌐 Available MCP Servers")
    for s in servers:
        st.sidebar.write(f"- **{s['name']}** → {s['url']}")

    # Chat history
    if "messages" not in st.session_state:
        st.session_state["messages"] = []

    for msg in st.session_state["messages"]:
        role = msg.get("role", "assistant")
        with st.chat_message(role):
            st.markdown(msg.get("content", ""))

    # Input box
    with st.form("user_input_form", clear_on_submit=True):
        user_input = st.text_input("Ask anything (Kubernetes / ArgoCD / Jenkins)...")
        submitted = st.form_submit_button("Send")

    if submitted and user_input:
        # Add user message
        st.session_state["messages"].append({"role": "user", "content": user_input})
        st.chat_message("user").markdown(user_input)

        # Auto decide server + tool
        decision = ask_gemini_for_server_and_tool(user_input)
        explanation = f"💡 {decision.get('explanation','')}"
        st.session_state["messages"].append({"role": "assistant", "content": explanation})
        st.chat_message("assistant").markdown(explanation)

        server = decision.get("server")
        tool = decision.get("tool")
        args = decision.get("args")

        if server and tool:
            # Set current server automatically
            if server in server_map:
                st.session_state["current_server"] = server_map[server]

            st.chat_message("assistant").markdown(
                f"🌐 Routed to **{server}**\n\n🔧 Executing *{tool}* with arguments:\n```json\n{json.dumps(args, indent=2)}\n```"
            )
            response = call_tool(tool, args or {})

            # Humanize response
            if GEMINI_AVAILABLE:
                pretty_answer = ask_gemini(
                    f"User asked: {user_input}\n\nRaw MCP response:\n{json.dumps(response, indent=2)}\n\n"
                    f"Answer in human-friendly language with bullet points if needed."
                )
                st.session_state["messages"].append({"role": "assistant", "content": pretty_answer})
                st.chat_message("assistant").markdown(pretty_answer)
            else:
                fallback = json.dumps(response, indent=2)
                st.session_state["messages"].append({"role": "assistant", "content": fallback})
                st.chat_message("assistant").markdown(fallback)
        else:
            # Fallback
            if GEMINI_AVAILABLE:
                answer = ask_gemini(user_input)
            else:
                answer = "❌ Could not decide server/tool. Please rephrase."
            st.session_state["messages"].append({"role": "assistant", "content": answer})
            st.chat_message("assistant").markdown(answer)


if __name__ == "__main__":
    main()
