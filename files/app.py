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
            timeout=30  # Reduced timeout for faster feedback
        )
        res.raise_for_status()

        # Handle streamed events from MCP server
        text = res.text.strip()
        if text.startswith("event:"):
            for line in text.splitlines():
                if line.startswith("data:"):
                    return json.loads(line[len("data:"):].strip())
        return res.json()
    except requests.exceptions.RequestException as e:
        return {"error": f"MCP server request failed: {str(e)}"}

def list_mcp_tools():
    """
    Get available tools from MCP server.
    """
    resp = call_mcp_server("tools/list")
    if "result" in resp and isinstance(resp["result"], dict):
        return resp["result"].get("tools", [])
    return []

def call_tool(name: str, arguments: dict):
    """
    Call a specific MCP tool with validated arguments.
    """
    if not name or not isinstance(arguments, dict):
        return {"error": "Invalid tool name or arguments"}
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
    Interpret user query to decide if it maps to an MCP tool or requires Gemini.
    """
    query_lower = query.lower().strip()
    if not query_lower:
        return {"tool": None, "args": None}

    # Keyword-based mapping for Kubernetes-related queries
    if "namespace" in query_lower:
        return {"tool": "namespace_list", "args": {}}
    elif "pod" in query_lower:
        # Extract namespace if specified, e.g., "pods in my-namespace"
        namespace = "default"
        if " in " in query_lower:
            parts = query_lower.split(" in ")
            if len(parts) > 1:
                namespace = parts[1].strip().split()[0]  # First word after "in"
        return {"tool": "kubectl_get", "args": {"resource": "pods", "namespace": namespace}}
    elif "service" in query_lower:
        return {"tool": "kubectl_get", "args": {"resource": "services", "namespace": "default"}}
    elif "deployment" in query_lower:
        return {"tool": "kubectl_get", "args": {"resource": "deployments", "namespace": "default"}}
    else:
        # Fallback to Gemini for general queries
        return {"tool": None, "args": None}

# ---------------- STREAMLIT UI ----------------
st.set_page_config(page_title="MCP Client UI", page_icon="⚡", layout="wide")

st.title("🤖 MCP Client – Kubernetes Assistant")

# Sidebar: Display available MCP tools
tools = list_mcp_tools()
if tools:
    st.sidebar.subheader("🔧 Available MCP Tools")
    for t in tools:
        st.sidebar.write(f"- {t['name']}: {t.get('description', 'No description')}")
else:
    st.sidebar.error("⚠️ Could not fetch tools from MCP server. Check server connectivity.")

# User input
st.subheader("💬 Query Kubernetes or Ask a Question")
user_query = st.text_input("Enter your query (e.g., 'show me all pods', 'list namespaces'):")

if st.button("Run Query"):
    if not user_query.strip():
        st.warning("Please enter a query first.")
    else:
        # Step 1: Interpret query
        with st.spinner("🤖 Processing query..."):
            decision = interpret_query(user_query)

        if decision["tool"]:
            # Step 2: Call MCP tool
            st.info(f"🔧 Executing MCP tool: `{decision['tool']}` with arguments: {decision['args']}")
            response = call_tool(decision["tool"], decision["args"])
            st.subheader("📡 MCP Server Response")
            if "error" in response:
                st.error(f"Error from MCP server: {response['error']}")
                st.markdown("**Possible Fixes**:")
                st.markdown("- Ensure the MCP server is running and accessible at the configured URL.")
                st.markdown("- Verify the Kubernetes cluster is configured correctly on the server.")
                st.markdown("- Check if the tool and parameters are supported by the MCP server.")
            else:
                st.json(response)
            
            # Step 3: Gemini explanation for context
            gemini_prompt = f"Explain the Kubernetes command or concept related to: {user_query}"
            gemini_response = ask_gemini(gemini_prompt)
            st.subheader("💡 Gemini Explanation")
            st.markdown(gemini_response)
        else:
            # Step 4: Direct Gemini response for non-Kubernetes queries
            st.subheader("💡 Gemini Answer")
            gemini_response = ask_gemini(user_query)
            st.markdown(gemini_response)
