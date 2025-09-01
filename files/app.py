import streamlit as st
import requests
import json

# --------------------
# Config
# --------------------
MCP_SERVER_URL = "http://18.234.91.216:3000/mcp"
GEMINI_API_KEY = "AIzaSyC7iRO4NnyQz144aEc6RiVUNzjL9C051V8"
GEMINI_MODEL = "gemini-1.5-flash"

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream"
}

# --------------------
# Helpers
# --------------------
def mcp_request(payload):
    try:
        r = requests.post(MCP_SERVER_URL, json=payload, headers=HEADERS, timeout=15)
        if r.status_code == 200:
            try:
                return r.json()
            except json.JSONDecodeError:
                return {"raw_response": r.text}
        return {"error": f"Status {r.status_code}", "body": r.text}
    except Exception as e:
        return {"error": str(e)}

def gemini_request(prompt: str):
    try:
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}",
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=15
        )
        if r.status_code == 200:
            return r.json()
        return {"error": f"Status {r.status_code}", "body": r.text}
    except Exception as e:
        return {"error": str(e)}

# --------------------
# UI Layout
# --------------------
st.set_page_config(page_title="MasaBot", page_icon="🤖", layout="centered")
st.title("🤖 MasaBot – MCP + Gemini UI")

st.markdown("### 🔗 Connected to MCP server")
st.write(f"**MCP URL:** {MCP_SERVER_URL}")

# --------------------
# User Input
# --------------------
user_input = st.text_input("💬 Ask something (Kubernetes / General):")

if st.button("Send") and user_input:
    # First, ask MCP which tools exist
    tools_payload = {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "listTools",
        "params": {}
    }
    tools_response = mcp_request(tools_payload)

    # Try calling a generic "ask" tool if available
    call_payload = {
        "jsonrpc": "2.0",
        "id": "2",
        "method": "callTool",
        "params": {
            "name": "kubernetes.query",   # 🔑 adjust based on listTools result
            "arguments": {"query": user_input}
        }
    }
    mcp_output = mcp_request(call_payload)

    # Gemini call
    gemini_output = gemini_request(user_input)

    # --------------------
    # Display results
    # --------------------
    st.subheader("📡 MCP Server - listTools")
    st.json(tools_response)

    st.subheader("📡 MCP Server - callTool Response")
    st.json(mcp_output)

    st.subheader("🌐 Gemini AI Response")
    st.json(gemini_output)
