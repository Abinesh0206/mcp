import streamlit as st
import requests
import json
import uuid

# ---------------- CONFIG ----------------
MCP_SERVER_URL = "http://18.234.91.216:3000/mcp"

# ---------------- STREAMLIT UI ----------------
st.set_page_config(page_title="MCP Client UI", page_icon="🤖", layout="wide")

st.title("🤖 MCP Client UI")
st.markdown("Chat with **MCP Server** (JSON-RPC 2.0)")

# Initialize chat history
if "messages" not in st.session_state:
    st.session_state["messages"] = []

# Sidebar config
st.sidebar.header("⚙️ Configuration")
server_url = st.sidebar.text_input("MCP Server URL", MCP_SERVER_URL)

# ---------------- FUNCTIONS ----------------
def query_mcp(query: str):
    """Send JSON-RPC request to MCP server"""
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),   # unique ID per request
            "method": "query",         # RPC method name (must match MCP server)
            "params": {"prompt": query}
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"
        }
        response = requests.post(server_url, data=json.dumps(payload), headers=headers, timeout=30)

        if response.status_code == 200:
            return response.json()
        else:
            return {"error": f"HTTP {response.status_code}: {response.text}"}

    except Exception as e:
        return {"error": str(e)}

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

    # Query MCP server
    response = query_mcp(query)

    # Handle server reply
    if "error" in response:
        reply = f"❌ Error: {response['error']}"
    else:
        reply = response.get("result", json.dumps(response, indent=2))

    # Save & display assistant message
    st.session_state["messages"].append({"role": "assistant", "content": reply})
    with st.chat_message("assistant"):
        st.markdown(reply)
