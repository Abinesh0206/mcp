import os
import json
import requests
import streamlit as st
from dotenv import load_dotenv
import google.generativeai as genai

# ---------------- CONFIG ----------------
load_dotenv()

MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://13.221.252.52:3000/mcp")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyA-iOGmYUxW000Nk6ORFFopi3cJE7J8wA4")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

# Configure Gemini
genai.configure(api_key=GEMINI_API_KEY)

# ---------------- HELPERS ----------------
def call_mcp_server(method: str, params: dict = None):
    """
    Send JSON-RPC request to MCP server and return response.
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
            timeout=60
        )
        res.raise_for_status()

        # Some MCP servers stream events → split by lines
        text = res.text.strip()
        if text.startswith("event:"):
            # Extract last "data:" block
            for line in text.splitlines():
                if line.startswith("data:"):
                    return json.loads(line[len("data:"):].strip())
        return res.json()
    except Exception as e:
        return {"error": str(e)}

def list_mcp_tools():
    """
    Get available tools from MCP server.
    """
    resp = call_mcp_server("tools/list")
    if "result" in resp:
        return resp["result"].get("tools", [])
    return []

def call_tool(name: str, arguments: dict):
    """
    Call a specific MCP tool.
    """
    resp = call_mcp_server("tools/call", {
        "name": name,
        "arguments": arguments
    })
    return resp

def ask_gemini(prompt: str):
    """
    Ask Gemini model for natural language processing.
    """
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Gemini error: {str(e)}"

def interpret_query(query: str):
    """
    Very basic interpreter: decide if query is for MCP or direct Gemini.
    """
    query_lower = query.lower()
    if "namespace" in query_lower:
        return {"tool": "namespace_list", "args": {}}
    elif "pod" in query_lower:
        return {"tool": "kubectl_get", "args": {"resource": "pods", "namespace": "default"}}
    else:
        return {"tool": None, "args": None}

# ---------------- STREAMLIT UI ----------------
st.set_page_config(page_title="MCP Client UI", page_icon="⚡", layout="wide")

st.title("🤖 MCP Client – K8s Assistant")

# Show tools from MCP server
tools = list_mcp_tools()
if tools:
    st.sidebar.subheader("🔧 Available MCP Tools")
    for t in tools:
        st.sidebar.write(f"- {t['name']}: {t.get('description','')}")
else:
    st.sidebar.write("⚠️ Could not fetch tools from MCP server.")

# User input
user_query = st.text_input("💬 Ask something (Kubernetes / General):")

if st.button("Run"):
    if not user_query.strip():
        st.warning("Please enter a query first.")
    else:
        # Step 1: Gemini interpretation
        st.write("🤖 Gemini thinking...")
        gemini_interpretation = ask_gemini(user_query)
        st.markdown(f"**Gemini Interpretation**\n\n{gemini_interpretation}")

        # Step 2: Decide tool vs direct answer
        decision = interpret_query(user_query)

        if decision["tool"]:  # If mapped to tool
            st.write(f"🔧 Calling MCP tool: `{decision['tool']}` with args: {decision['args']}")
            response = call_tool(decision["tool"], decision["args"])
            st.subheader("📡 MCP Server Response:")
            st.json(response)
        else:  # General Q → Gemini direct answer
            st.subheader("💡 Gemini Answer:")
            st.write(gemini_interpretation)
