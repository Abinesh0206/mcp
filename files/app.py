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

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyA-iOGmYUxW000Nk6ORFFopi3cJE7J8wA4")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

genai.configure(api_key=GEMINI_API_KEY)


# ---------------- LOAD SERVERS ----------------
def load_servers():
    """Load server list from servers.json, fallback to default."""
    try:
        with open("servers.json") as f:
            data = json.load(f)
        return data.get("servers", [])
    except Exception as e:
        return [{
            "name": "default",
            "url": "http://127.0.0.1:3000/mcp",
            "description": f"Fallback server: {e}"
        }]


servers = load_servers()

# Initialize current server in session state
if "current_server" not in st.session_state:
    st.session_state["current_server"] = servers[0]["url"]


def get_current_server_url():
    return st.session_state.get("current_server", servers[0]["url"])


# ---------------- HELPERS ----------------
def call_mcp_server(method: str, params: dict = None):
    """Call MCP server with JSON-RPC payload."""
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
        if text.startswith("event:"):  # handle SSE style response
            for line in text.splitlines():
                if line.startswith("data:"):
                    return json.loads(line[len("data:"):].strip())
        return res.json()
    except requests.exceptions.RequestException as e:
        return {"error": f"MCP server request failed: {str(e)}"}


def list_mcp_tools():
    """Fetch available MCP tools."""
    resp = call_mcp_server("tools/list")
    if "result" in resp and isinstance(resp["result"], dict):
        return resp["result"].get("tools", [])
    return []


def call_tool(name: str, arguments: dict):
    """Execute MCP tool by name with arguments."""
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
    """Ask Gemini for free-text natural language generation."""
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(prompt)
        return response.text
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
    """Use Gemini to map user query -> MCP tool + arguments."""
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
    {{
      "tool": "<tool_name>" | null,
      "args": {{}} | null,
      "explanation": "Short explanation"
    }}
    """

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
                parsed = {"tool": None, "args": None, "explanation": f"Gemini invalid: {text}"}

        parsed["args"] = sanitize_args(parsed.get("args"))
        return parsed

    except Exception as e:
        return {"tool": None, "args": None, "explanation": f"Gemini error: {str(e)}"}


# ---------------- STREAMLIT APP ----------------
def main():
    st.set_page_config(page_title="MCP Chat Assistant", page_icon="⚡", layout="wide")
    st.title("🤖 Masa Bot Assistant")

    # Sidebar: select MCP server
    st.sidebar.subheader("🌐 Select MCP Server")
    server_names = [f"{s['name']} ({s['url']})" for s in servers]
    choice = st.sidebar.radio("Available Servers:", server_names)
    st.session_state["current_server"] = next(
        s["url"] for s in servers if choice.startswith(s["name"])
    )

    # Show tools for current server
    tools = list_mcp_tools()
    if tools:
        st.sidebar.subheader("🔧 Available MCP Tools")
        for t in tools:
            st.sidebar.write(f"- {t['name']}: {t.get('description','')}")
    else:
        st.sidebar.error("⚠ Could not fetch tools from MCP server.")

    # Initialize chat history
    if "messages" not in st.session_state:
        st.session_state["messages"] = []

    # Init create application flow
    if "create_flow" not in st.session_state:
        st.session_state["create_flow"] = None
        st.session_state["create_data"] = {}

    # Display chat history
    for msg in st.session_state["messages"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Chat input
    with st.form("user_input_form", clear_on_submit=True):
        user_input = st.text_input("Ask Kubernetes or ArgoCD something...")
        submitted = st.form_submit_button("Send")

    if submitted and user_input:
        # Handle "create application" flow
        if user_input.lower().strip() == "create application" and not st.session_state["create_flow"]:
            st.session_state["create_flow"] = "name"
            st.session_state["create_data"] = {}
            prompt = "Please provide: name"
            st.session_state["messages"].append({"role": "assistant", "content": prompt})
            st.chat_message("assistant").markdown(prompt)
            return

        if st.session_state["create_flow"]:
            step = st.session_state["create_flow"]
            data = st.session_state["create_data"]

            if step == "name":
                data["name"] = user_input
                st.session_state["create_flow"] = "project"
                prompt = "Please provide: project"
            elif step == "project":
                data["project"] = user_input
                st.session_state["create_flow"] = "repo_url"
                prompt = "Please provide: repo_url"
            elif step == "repo_url":
                data["repo_url"] = user_input
                st.session_state["create_flow"] = "path"
                prompt = "Please provide: path"
            elif step == "path":
                data["path"] = user_input
                st.session_state["create_flow"] = "dest_ns"
                prompt = "Please provide: dest_ns"
            elif step == "dest_ns":
                data["dest_ns"] = user_input
                st.session_state["create_flow"] = "done"
                data["cluster"] = "https://kubernetes.default.svc"
                data["sync_policy"] = "automated"
                prompt = f"✅ Application data collected:\n```json\n{json.dumps(data, indent=2)}\n```\n\nNow creating application..."
                # Call create tool
                resp = call_tool("create_application", data)

                # Natural language summary for creation
                pretty_create = ask_gemini(
                    f"A new ArgoCD application was created with this response:\n"
                    f"{json.dumps(resp, indent=2)}\n\n"
                    f"Explain clearly in human language what was created (name, namespace, repo, path, cluster, project)."
                )
                
                # Fetch live application status
                app_name = data["name"]
                status_resp = call_tool("get_application", {"application_name": app_name})
                
                pretty_status = ask_gemini(
                    f"Here is the status of ArgoCD application '{app_name}':\n"
                    f"{json.dumps(status_resp, indent=2)}\n\n"
                    f"Explain in human-friendly language the current sync status, health, and summary."
                )
                
                prompt = f"✅ Application created successfully!\n\n{pretty_create}\n\n📊 Current Status:\n{pretty_status}"
                
                st.session_state["create_flow"] = None

            st.session_state["messages"].append({"role": "assistant", "content": prompt})
            st.chat_message("assistant").markdown(prompt)
            return

        # Normal flow (Gemini + MCP)
        st.session_state["messages"].append({"role": "user", "content": user_input})
        st.chat_message("user").markdown(user_input)

        decision = ask_gemini_for_tool_decision(user_input)
        explanation = f"💡 {decision.get('explanation','')}"
        st.session_state["messages"].append({"role": "assistant", "content": explanation})
        st.chat_message("assistant").markdown(explanation)

        if decision["tool"]:
            st.chat_message("assistant").markdown(
                f"🔧 Executing *{decision['tool']}* with arguments:\n```json\n{json.dumps(decision['args'], indent=2)}\n```"
            )
            response = call_tool(decision["tool"], decision["args"])
            pretty_answer = ask_gemini(
                f"User asked: {user_input}\n\n"
                f"Here is the raw MCP response:\n{json.dumps(response, indent=2)}\n\n"
                f"Answer in natural human-friendly language. "
                f"If multiple items (pods, apps, projects, services), format as bullet points."
            )
            st.session_state["messages"].append({"role": "assistant", "content": pretty_answer})
            st.chat_message("assistant").markdown(pretty_answer)
        else:
            answer = ask_gemini(user_input)
            st.session_state["messages"].append({"role": "assistant", "content": answer})
            st.chat_message("assistant").markdown(answer)


if __name__ == "__main__":
    main()
