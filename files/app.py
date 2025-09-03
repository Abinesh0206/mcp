import streamlit as st
import requests
import uuid
import json

# ---------------- CONFIG ----------------
MCP_SERVER_URL = "http://13.221.252.52:3000/mcp"

# ---------------- STREAMLIT UI ----------------
st.set_page_config(page_title="MCP Client UI", page_icon="🤖", layout="wide")

st.title("🤖 MCP Client UI")
st.markdown("Chat with **MCP Server** (JSON-RPC 2.0 + call_tool)")

# Initialize chat history
if "messages" not in st.session_state:
    st.session_state["messages"] = []

# ---------------- FUNCTIONS ----------------
def call_tool(tool_name: str, arguments: dict):
    """Send JSON-RPC request to MCP server using call_tool"""
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
        response = requests.post(
            MCP_SERVER_URL,
            data=json.dumps(payload),
            headers=headers,
            timeout=30
        )

        if response.status_code != 200:
            return {"error": f"HTTP {response.status_code}: {response.text}"}

        try:
            return response.json()
        except Exception:
            return {"raw": response.text}

    except Exception as e:
        return {"error": str(e)}

# ---------------- CHAT UI ----------------
for msg in st.session_state["messages"]:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Input fields
tool_name = st.text_input("Tool name (e.g., kubectl_get, namespace_list)")
arguments_text = st.text_area("Arguments (JSON)", value='{"resource": "pods", "namespace": "default"}')

if st.button("Run Tool"):
    try:
        arguments = json.loads(arguments_text)
    except Exception as e:
        st.error(f"Invalid JSON in arguments: {e}")
        arguments = {}

    st.session_state["messages"].append({"role": "user", "content": f"{tool_name} {arguments}"})
    with st.chat_message("user"):
        st.markdown(f"**{tool_name}** with args: `{arguments}`")

    response = call_tool(tool_name, arguments)

    if "error" in response:
        reply = f"❌ Error: {response['error']}"
    elif "result" in response:
        reply = f"✅ Result: {json.dumps(response['result'], indent=2)}"
    elif "raw" in response:
        reply = f"📡 Raw response:\n\n```\n{response['raw']}\n```"
    else:
        reply = json.dumps(response, indent=2)

    st.session_state["messages"].append({"role": "assistant", "content": reply})
    with st.chat_message("assistant"):
        st.markdown(reply)
