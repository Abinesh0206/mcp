import os
import json
import requests
import streamlit as st
from dotenv import load_dotenv
import google.generativeai as genai

# ---------------- CONFIG ----------------
load_dotenv()

MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://13.221.252.52:3000/mcp")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyC7iRO4NnyQz144aEc6RiVUNzjL9C051V8")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

# Configure Gemini
genai.configure(api_key=GEMINI_API_KEY)

# ---------------- HELPERS ----------------
def call_mcp_server(method: str, params: dict = None):
    """Send a JSON-RPC request to the MCP server."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or {}
    }
    try:
        res = requests.post(
            MCP_SERVER_URL,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream"
            },
            json=payload,
            timeout=30
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
    """Fetch list of available MCP tools."""
    resp = call_mcp_server("tools/list")
    if "result" in resp and isinstance(resp["result"], dict):
        return resp["result"].get("tools", [])
    return []


def call_tool(name: str, arguments: dict):
    """Execute a specific MCP tool with arguments."""
    if not name or not isinstance(arguments, dict):
        return {"error": "Invalid tool name or arguments"}
    return call_mcp_server("tools/call", {"name": name, "arguments": arguments})


def ask_gemini(prompt: str):
    """Send a free-text query to Gemini and return its response."""
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Gemini error: {str(e)}"


def sanitize_args(args: dict):
    """Fix Gemini argument naming mismatches for MCP server."""
    if not args:
        return {}
    fixed = args.copy()

    # Normalize key names
    if "resource" in fixed and "resourceType" not in fixed:
        fixed["resourceType"] = fixed.pop("resource")

    # Ensure namespace is set if required
    if fixed.get("resourceType") == "pods" and "namespace" not in fixed:
        fixed["namespace"] = "default"

    return fixed


def ask_gemini_for_tool_decision(query: str):
    """
    Ask Gemini whether the query needs MCP tool execution.
    Always enforce JSON output with correct argument names.
    """
    instruction = f"""
You are an AI agent that decides if a user query requires calling a Kubernetes MCP tool.

Query: "{query}"

Respond ONLY in strict JSON with this structure, no extra text:
{{
  "tool": "kubectl_get" | "kubectl_create" | "kubectl_delete" | "kubectl_describe" | "install_helm_chart" | null,
  "args": {{}} or null,
  "explanation": "Short explanation in plain English"
}}

Important:
- Use key `resourceType` (not `resource`).
- Add `namespace` if required (e.g., for pods, deployments).
"""
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(instruction)
        text = response.text.strip()

        # Try direct JSON parse
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            # Fallback: extract JSON substring
            start = text.find("{")
            end = text.rfind("}") + 1
            if start != -1 and end != -1:
                parsed = json.loads(text[start:end])
            else:
                return {"tool": None, "args": None, "explanation": f"Gemini invalid response: {text}"}

        parsed["args"] = sanitize_args(parsed.get("args"))
        return parsed
    except Exception as e:
        return {"tool": None, "args": None, "explanation": f"Gemini error: {str(e)}"}


# ---------------- STREAMLIT UI ----------------
st.set_page_config(page_title="MCP Client UI", page_icon="⚡", layout="wide")
st.title("🤖 MCP Client – Kubernetes Assistant")

# Sidebar with available tools
tools = list_mcp_tools()
if tools:
    st.sidebar.subheader("🔧 Available MCP Tools")
    for t in tools:
        st.sidebar.write(f"- {t['name']}: {t.get('description', 'No description')}")
else:
    st.sidebar.error("⚠️ Could not fetch tools from MCP server. Check server connectivity.")

# User input
st.subheader("💬 Query Kubernetes or Ask a Question")
user_query = st.text_input("Enter your query (e.g., 'create namespace xyz', 'install harbor'):")

if st.button("Run Query"):
    if not user_query.strip():
        st.warning("Please enter a query first.")
    else:
        with st.spinner("🤖 Gemini is thinking..."):
            decision = ask_gemini_for_tool_decision(user_query)

        # Show Gemini’s explanation first
        st.subheader("💡 Gemini Explanation")
        st.markdown(decision.get("explanation", ""))

        if decision["tool"]:
            st.info(f"🔧 Executing MCP tool: `{decision['tool']}` with arguments: {decision['args']}")
            response = call_tool(decision["tool"], decision["args"])
            st.subheader("📡 MCP Server Response")
            if "error" in response:
                st.error(f"Error from MCP server: {response['error']}")
            else:
                st.json(response)
        else:
            st.subheader("💡 Gemini Direct Answer")
            st.markdown(ask_gemini(user_query))
