import os
import json
import requests
import streamlit as st
import pandas as pd
from dotenv import load_dotenv
import google.generativeai as genai
from datetime import datetime, timezone

# ---------------- CONFIG ----------------
load_dotenv()

MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://13.221.252.52:3000/mcp")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyDHN1tGJLFojK65QgcxnZm8QApZdXSDl1w")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

# Configure Gemini
genai.configure(api_key=GEMINI_API_KEY)

# ---------------- HELPERS ----------------
def call_mcp_server(method: str, params: dict = None):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    try:
        res = requests.post(
            MCP_SERVER_URL,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
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
        return {"error": f"MCP server request failed: {str(e)}"}


def list_mcp_tools():
    resp = call_mcp_server("tools/list")
    if "result" in resp and isinstance(resp["result"], dict):
        return resp["result"].get("tools", [])
    return []


def call_tool(name: str, arguments: dict):
    if not name or not isinstance(arguments, dict):
        return {"error": "Invalid tool name or arguments"}
    return call_mcp_server("tools/call", {"name": name, "arguments": arguments})


def humanize_age(created_at: str) -> str:
    """Convert createdAt timestamp → AGE like kubectl."""
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


def render_mcp_response(response: dict):
    """Pretty-print MCP server response like kubectl."""
    if "error" in response:
        return f"❌ Error: {response['error']}"

    result = response.get("result", {})

    # Case 1: Text response
    content = result.get("content", [])
    if isinstance(content, list) and len(content) > 0:
        text_blocks = [c.get("text", "") for c in content if c.get("type") == "text"]
        if text_blocks:
            return "```\n" + "\n".join(text_blocks).strip() + "\n```"

    # Case 2: Kubernetes items (namespaces, pods, etc.)
    if "items" in result:
        items = result.get("items", [])
        if not items:
            return "ℹ️ No resources found in the cluster."

        # Detect if this is namespaces
        if all("name" in i and "status" in i and "createdAt" in i for i in items):
            rows = []
            for i in items:
                rows.append({
                    "NAME": i["name"],
                    "STATUS": i["status"],
                    "AGE": humanize_age(i["createdAt"])
                })
            df = pd.DataFrame(rows)
            return "```\n" + df.to_string(index=False) + "\n```"

        # fallback generic
        df = pd.DataFrame(items)
        return df.to_markdown(index=False)

    return "⚠️ No usable response from MCP server."


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


def ask_gemini_for_tool_decision(query: str):
    instruction = f"""
You are an AI agent that decides if a user query requires calling a Kubernetes MCP tool.

Query: "{query}"

Respond ONLY in strict JSON:
{{
  "tool": "kubectl_get" | "kubectl_create" | "kubectl_delete" | "kubectl_describe" | "install_helm_chart" | null,
  "args": {{}} or null,
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
                return {"tool": None, "args": None, "explanation": f"Gemini invalid: {text}"}

        parsed["args"] = sanitize_args(parsed.get("args"))
        return parsed
    except Exception as e:
        return {"tool": None, "args": None, "explanation": f"Gemini error: {str(e)}"}


# ---------------- STREAMLIT APP ----------------
st.set_page_config(page_title="MCP Chat Assistant", page_icon="⚡", layout="wide")
st.title("🤖 MCP Client – Kubernetes Assistant")

# Init chat history
if "messages" not in st.session_state:
    st.session_state["messages"] = []

# Sidebar tools
tools = list_mcp_tools()
if tools:
    st.sidebar.subheader("🔧 Available MCP Tools")
    for t in tools:
        st.sidebar.write(f"- {t['name']}: {t.get('description', '')}")
else:
    st.sidebar.error("⚠️ Could not fetch tools from MCP server.")

# Render chat history
for msg in st.session_state["messages"]:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Chat input
if prompt := st.chat_input("Ask Kubernetes something..."):
    # Store user message
    st.chat_message("user").markdown(prompt)
    st.session_state["messages"].append({"role": "user", "content": prompt})

    # Tool decision
    decision = ask_gemini_for_tool_decision(prompt)
    explanation = f"💡 {decision.get('explanation', '')}"
    st.chat_message("assistant").markdown(explanation)
    st.session_state["messages"].append({"role": "assistant", "content": explanation})

    if decision["tool"]:
        st.chat_message("assistant").markdown(
            f"🔧 Executing **{decision['tool']}** with arguments:\n```json\n{json.dumps(decision['args'], indent=2)}\n```"
        )
        response = call_tool(decision["tool"], decision["args"])
        output = render_mcp_response(response)

        st.chat_message("assistant").markdown(output)
        st.session_state["messages"].append({"role": "assistant", "content": output})
    else:
        answer = ask_gemini(prompt)
        st.chat_message("assistant").markdown(answer)
        st.session_state["messages"].append({"role": "assistant", "content": answer})
