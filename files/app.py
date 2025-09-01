import streamlit as st
import requests
import json

# --------------------
# Config
# --------------------
MCP_SERVER_URL = "http://18.234.91.216:3000/mcp"
GEMINI_API_KEY = "AIzaSyC7iRO4NnyQz144aEc6RiVUNzjL9C051V8"
GEMINI_MODEL = "gemini-1.5-flash"

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
    # --------------------
    # Call MCP server (JSON-RPC style)
    # --------------------
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "query",
            "params": {"query": user_input}
        }
        mcp_response = requests.post(MCP_SERVER_URL, json=payload, timeout=10)
        if mcp_response.status_code == 200:
            mcp_output = mcp_response.json()
        else:
            mcp_output = {"error": f"Status {mcp_response.status_code}", "body": mcp_response.text}
    except Exception as e:
        mcp_output = {"error": str(e)}

    # --------------------
    # Call Gemini API (correct auth)
    # --------------------
    try:
        gemini_response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}",
            json={
                "contents": [
                    {"parts": [{"text": user_input}]}
                ]
            },
            timeout=10
        )
        if gemini_response.status_code == 200:
            gemini_output = gemini_response.json()
        else:
            gemini_output = {"error": f"Status {gemini_response.status_code}", "body": gemini_response.text}
    except Exception as e:
        gemini_output = {"error": str(e)}

    # --------------------
    # Display results
    # --------------------
    st.subheader("📡 MCP Server Response")
    st.json(mcp_output)

    st.subheader("🌐 Gemini AI Response")
    st.json(gemini_output)
