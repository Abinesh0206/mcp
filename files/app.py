import os
import json
import requests
import streamlit as st
from dotenv import load_dotenv
import google.generativeai as genai

# ---------------- CONFIG ----------------
load_dotenv()

MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:3000/mcp")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

# Configure Gemini
genai.configure(api_key=GEMINI_API_KEY)

# ---------------- HELPERS ----------------
def call_mcp_server(method: str, params: dict = None):
    """
    Send a JSON-RPC request to MCP server
    """
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
    resp = call_mcp_server("tools/list")
    if "result" in resp and isinstance(resp["result"], dict):
        return resp["result"].get("tools", [])
    return []


def call_tool(name: str, arguments: dict):
    if not name or not isinstance(arguments, dict):
        return {"error": "Invalid tool name or arguments"}
    return call_mcp_server("tools/call", {"name": name, "arguments": arguments})


def ask_gemini(prompt: str):
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Gemini error: {str(e)}"


def interpret_query(query: str):
    """
    Map user query → MCP tool & arguments
    """
    query_lower = query.lower().strip()
    if not query_lower:
        return {"tool": None, "args": None}

    # Namespace queries
    if "namespace" in query_lower:
        return {"tool": "kubectl_get", "args": {"resourceType": "namespaces"}}

    # Pods
    if "pod" in query_lower:
        namespace = "default"
        if " in " in query_lower:
            namespace = query_lower.split(" in ")[-1].strip()
        return {"tool": "kubectl_get", "args": {"resourceType": "pods", "namespace": namespace}}

    # Services
    if "service" in query_lower:
        return {"tool": "kubectl_get", "args": {"resourceType": "services", "namespace": "default"}}

    # Deployments
    if "deployment" in query_lower:
        return {"tool": "kubectl_get", "args": {"resourceType": "deployments", "namespace": "default"}}

    # Describe pod
    if "describe" in query_lower and "pod" in query_lower:
        parts = query_lower.split()
        try:
            pod_index = parts.index("pod")
            name = parts[pod_index + 1]
        except Exception:
            name = ""
        namespace = "default"
        if " in " in query_lower:
            namespace = query_lower.split(" in ")[-1].strip()
        return {
            "tool": "kubectl_describe",
            "args": {"resourceType": "pod", "name": name, "namespace": namespace}
        }

    # fallback → Gemini
    return {"tool": None, "args": None}


# ---------------- STREAMLIT UI ----------------
st.set_page_config(page_title="MCP Client UI", page_icon="⚡", layout="wide")

st.title("🤖 MCP Client – Kubernetes Assistant")

# Sidebar
tools = list_mcp_tools()
if tools:
    st.sidebar.subheader("🔧 Available MCP Tools")
    for t in tools:
        st.sidebar.write(f"- {t['name']}: {t.get('description', 'No description')}")
else:
    st.sidebar.error("⚠️ Could not fetch tools from MCP server. Check server connectivity.")

# User input
st.subheader("💬 Query Kubernetes or Ask a Question")
user_query = st.text_input("Enter your query (e.g., 'show me all pods', 'describe pod xyz'):")

if st.button("Run Query"):
    if not user_query.strip():
        st.warning("Please enter a query first.")
    else:
        with st.spinner("🤖 Processing query..."):
            decision = interpret_query(user_query)

        if decision["tool"]:
            st.info(f"🔧 Executing MCP tool: `{decision['tool']}` with arguments: {decision['args']}")
            response = call_tool(decision["tool"], decision["args"])
            st.subheader("📡 MCP Server Response")
            if "error" in response:
                st.error(f"Error from MCP server: {response['error']}")
            else:
                st.json(response)

            gemini_prompt = f"Explain the Kubernetes command or result related to: {user_query}"
            st.subheader("💡 Gemini Explanation")
            st.markdown(ask_gemini(gemini_prompt))
        else:
            st.subheader("💡 Gemini Answer")
            st.markdown(ask_gemini(user_query))
