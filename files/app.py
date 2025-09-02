import streamlit as st
import requests
import json
import uuid

# ---------------- CONFIG ----------------
MCP_SERVER_URL = "http://18.234.91.216:3000/mcp"
GEMINI_API_KEY = "AIzaSyA-iOGmYUxW000Nk6ORFFopi3cJE7J8wA4"
GEMINI_MODEL = "gemini-1.5-flash"

# ---------------- STREAMLIT UI ----------------
st.set_page_config(page_title="MCP Client UI", page_icon="🤖", layout="wide")

st.title("🤖 MCP Client UI")
st.markdown("Chat with **MCP Server** (JSON-RPC 2.0 + Gemini)")

# Initialize chat history
if "messages" not in st.session_state:
    st.session_state["messages"] = []

# Sidebar config
st.sidebar.header("⚙️ Configuration")
server_url = st.sidebar.text_input("MCP Server URL", MCP_SERVER_URL)
api_key = st.sidebar.text_input("Gemini API Key", GEMINI_API_KEY, type="password")
model = st.sidebar.text_input("Gemini Model", GEMINI_MODEL)

# ---------------- FUNCTIONS ----------------
def query_mcp(tool_name: str, arguments: dict):
    """Send JSON-RPC call_tool request to MCP server"""
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "call_tool",
        "params": {
            "name": tool_name,
            "arguments": arguments
        }
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream"
    }

    try:
        response = requests.post(server_url, data=json.dumps(payload), headers=headers, timeout=30)

        if response.status_code != 200:
            return {"error": f"HTTP {response.status_code}: {response.text}"}

        # Try to decode as JSON
        try:
            return response.json()
        except Exception:
            return {"raw": response.text}   # fallback for event-stream / plain text

    except Exception as e:
        return {"error": str(e)}

def parse_user_query(query: str):
    """
    Simple mapping from user text -> tool + arguments.
    Extend this with Gemini later if you want smarter mapping.
    """
    if "namespace" in query.lower():
        return "kubectl_get", {"resource": "namespaces"}
    elif "pods" in query.lower():
        return "kubectl_get", {"resource": "pods", "namespace": "default"}
    else:
        # default fall back
        return "kubectl_get", {"resource": "namespaces"}

# ---------------- CHAT UI ----------------
for msg in st.session_state["messages"]:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Chat input
if query := st.chat_input("Type your query..."):
    # Save & display user message
    st.session_state["messages"].append({"role": "user", "content": query})
    with st.chat_message("user"):
        st.markdown(query)

    # Convert text -> tool call
    tool_name, args = parse_user_query(query)

    # Query MCP server
    response = query_mcp(tool_name, args)

    # Handle server reply
    if "error" in response:
        reply = f"❌ Error: {response['error']}"
    elif "result" in response:
        reply = json.dumps(response["result"], indent=2)
    elif "raw" in response:
        reply = f"📡 Raw response:\n\n```\n{response['raw']}\n```"
    else:
        reply = json.dumps(response, indent=2)

    # Save & display assistant message
    st.session_state["messages"].append({"role": "assistant", "content": reply})
    with st.chat_message("assistant"):
        st.markdown(reply)
