import streamlit as st
import requests
import os

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
    # Call MCP server
    try:
        mcp_response = requests.post(
            MCP_SERVER_URL,
            json={"query": user_input},
            timeout=10
        )
        mcp_output = mcp_response.json()
    except Exception as e:
        mcp_output = {"error": str(e)}

    # Call Gemini API
    try:
        headers = {"Authorization": f"Bearer {GEMINI_API_KEY}"}
        gemini_response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}",
            headers=headers,
            json={"contents": [{"parts": [{"text": user_input}]}]},
            timeout=10
        )
        gemini_output = gemini_response.json()
    except Exception as e:
        gemini_output = {"error": str(e)}

    # --------------------
    # Display results
    # --------------------
    st.subheader("📡 MCP Server Response")
    st.json(mcp_output)

    st.subheader("🌐 Gemini AI Response")
    st.json(gemini_output)
