import streamlit as st
import requests
import json

# ---------------- CONFIG ----------------
MCP_SERVER_URL = "http://18.234.91.216:3000/mcp"
GEMINI_API_KEY = "AIzaSyA-iOGmYUxW000Nk6ORFFopi3cJE7J8wA4"
GEMINI_MODEL = "gemini-1.5-flash"

# ---------------- STREAMLIT UI ----------------
st.set_page_config(page_title="MCP Client UI", page_icon="🤖", layout="wide")

st.title("🤖 MCP Client UI")
st.markdown("Chat with **MCP Server** powered by Gemini")

# Initialize chat history in session
if "messages" not in st.session_state:
    st.session_state["messages"] = []

# Sidebar config
st.sidebar.header("⚙️ Configuration")
server_url = st.sidebar.text_input("MCP Server URL", MCP_SERVER_URL)
api_key = st.sidebar.text_input("Gemini API Key", GEMINI_API_KEY, type="password")
model = st.sidebar.text_input("Gemini Model", GEMINI_MODEL)

# ---------------- FUNCTIONS ----------------
def query_mcp(query: str):
    """Send query to MCP server"""
    try:
        payload = {
            "query": query,
            "model": model,
            "apiKey": api_key
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

# ---------------- CHAT INTERFACE ----------------
for msg in st.session_state["messages"]:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Input box at bottom
if query := st.chat_input("Type your query..."):
    # Show user message
    st.session_state["messages"].append({"role": "user", "content": query})
    with st.chat_message("user"):
        st.markdown(query)

    # Send query to MCP server
    response = query_mcp(query)

    # Show response
    if "error" in response:
        reply = f"❌ Error: {response['error']}"
    else:
        # Some servers return {"reply": "..."} others raw JSON
        reply = response.get("reply", json.dumps(response, indent=2))

    st.session_state["messages"].append({"role": "assistant", "content": reply})
    with st.chat_message("assistant"):
        st.markdown(reply)
