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

def build_mcp_payload(query: str):
    """ Map user query to correct MCP tool call """
    q = query.lower()

    if "namespace" in q:
        return {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tools/kubectl_get",
            "params": {
                "resourceType": "namespaces",
                "name": "",
                "namespace": "",
                "allNamespaces": True,
                "output": "json"
            }
        }
    elif "node" in q:
        return {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tools/kubectl_get",
            "params": {
                "resourceType": "nodes",
                "name": "",
                "namespace": "",
                "allNamespaces": True,
                "output": "json"
            }
        }
    elif "pod" in q:
        return {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tools/kubectl_get",
            "params": {
                "resourceType": "pods",
                "name": "",
                "namespace": "",
                "allNamespaces": True,
                "output": "json"
            }
        }
    else:
        return {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "ping",
            "params": {}
        }

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
        except Exception:
            st.error("⚠️ Gemini error: " + str(g_json))
            st.stop()

        st.write("### 🤖 Gemini Interpretation")
        st.info(gemini_text)

        # Step 2: Build MCP payload
        mcp_payload = build_mcp_payload(query)

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

        try:
            m_res = requests.post(MCP_SERVER_URL, json=mcp_payload, headers=headers)
            m_text = m_res.text.strip()

            # If response is wrapped in SSE, extract last data block
            if "data:" in m_text:
                m_text = m_text.split("data:")[-1].strip()

            try:
                m_json = json.loads(m_text)
            except Exception:
                m_json = {"raw_response": m_text}

        except Exception as e:
            st.error("⚠️ MCP Server error: " + str(e))
            st.stop()

        # Step 3: Show MCP server response
        st.write("### 📡 MCP Server Response")
        st.json(m_json)
