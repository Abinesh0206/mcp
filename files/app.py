import streamlit as st
import requests
import json

# ---------------- CONFIG ----------------
MCP_SERVER_URL = "http://18.234.91.216:3000/mcp"
GEMINI_API_KEY = "AIzaSyA-iOGmYUxW000Nk6ORFFopi3cJE7J8wA4"
GEMINI_MODEL = "gemini-1.5-flash"

# Gemini endpoint
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"

st.set_page_config(page_title="MasaBot – MCP + Gemini", page_icon="🤖", layout="centered")
st.title("🤖 MasaBot – MCP + Gemini UI")

# ---------------- CHAT INPUT ----------------
query = st.text_input("💬 Ask something (Kubernetes / General):", "")

if st.button("Ask") and query:
    with st.spinner("Gemini thinking..."):

        # Step 1: Ask Gemini to interpret
        gemini_payload = {
            "contents": [{
                "parts": [{"text": query}]
            }]
        }
        g_res = requests.post(GEMINI_URL, json=gemini_payload)
        g_json = g_res.json()

        try:
            gemini_text = g_json["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:
            st.error("⚠️ Gemini error: " + str(g_json))
            st.stop()

        st.write("### 🤖 Gemini Interpretation")
        st.info(gemini_text)

        # Step 2: Convert query to MCP call
        # Simple mapping: if query contains "namespace", call kubectl_get namespaces
        if "namespace" in query.lower():
            mcp_payload = {
                "jsonrpc": "2.0",
                "id": "1",
                "method": "tools/kubectl_get",
                "params": {
                    "resourceType": "namespaces",
                    "namespace": "",      # not needed for namespaces
                    "name": "",           # empty means list all
                    "allNamespaces": True,
                    "output": "json"
                }
            }
        else:
            mcp_payload = {
                "jsonrpc": "2.0",
                "id": "1",
                "method": "ping",
                "params": {}
            }

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"
        }

        try:
            m_res = requests.post(MCP_SERVER_URL, json=mcp_payload, headers=headers)
            m_text = m_res.text.strip()

            try:
                m_json = json.loads(m_text)
            except:
                m_json = {"raw_response": m_text}

        except Exception as e:
            st.error("⚠️ MCP Server error: " + str(e))
            st.stop()

        # Step 3: Show MCP server response
        st.write("### 📡 MCP Server Response")
        st.success(m_json)
