import os
import json
import requests
import streamlit as st

# ---------------- CONFIG ----------------
MCP_HOST = os.getenv("MCP_HOST", "18.234.91.216")
MCP_PORT = os.getenv("MCP_PORT", "3000")
MCP_SERVER_URL = f"http://{MCP_HOST}:{MCP_PORT}/"   # MCP server root (not /mcp)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "YOUR_GEMINI_API_KEY")
GEMINI_MODEL = "gemini-1.5-flash"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"

st.set_page_config(page_title="MasaBot – MCP + Gemini", page_icon="🤖", layout="centered")
st.title("🤖 MasaBot – MCP + Gemini UI")

query = st.text_input("💬 Ask something (Kubernetes / General):", "")


# ---------------- HELPERS ----------------
def build_mcp_payload(query: str):
    q = query.lower()

    if "namespace" in q:
        return {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "kubectl_get",
                "arguments": {
                    "resourceType": "namespaces",
                    "output": "json"
                }
            }
        }
    elif "node" in q:
        return {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "kubectl_get",
                "arguments": {
                    "resourceType": "nodes",
                    "output": "json"
                }
            }
        }
    elif "pod" in q:
        return {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "kubectl_get",
                "arguments": {
                    "resourceType": "pods",
                    "namespace": "default",
                    "output": "json"
                }
            }
        }
    return {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}}


# ---------------- MAIN ----------------
if st.button("Ask") and query:
    with st.spinner("Gemini thinking..."):
        # Step 1: Ask Gemini
        g_payload = {"contents": [{"parts": [{"text": query}]}]}
        g_res = requests.post(GEMINI_URL, json=g_payload)
        g_json = g_res.json()

        try:
            gemini_text = g_json["candidates"][0]["content"]["parts"][0]["text"]
        except Exception:
            st.error("⚠ Gemini error: " + str(g_json))
            st.stop()

        st.write("### 🤖 Gemini Interpretation")
        st.info(gemini_text)

        # Step 2: Call MCP server
        mcp_payload = build_mcp_payload(query)
        headers = {"Content-Type": "application/json"}

        try:
            m_res = requests.post(MCP_SERVER_URL, json=mcp_payload, headers=headers, timeout=10)
            m_json = m_res.json()
        except Exception as e:
            st.error(f"⚠ MCP connection failed: {e}")
            st.stop()

        st.write("### 📡 MCP Server Response")
        st.json(m_json)

        # Step 3: Count resources if possible
        try:
            items = []
            if "result" in m_json and isinstance(m_json["result"], dict):
                if "content" in m_json["result"]:
                    text_data = m_json["result"]["content"][0]["text"]
                    parsed = json.loads(text_data)
                    items = parsed.get("items", [])

            if "namespace" in query.lower():
                st.success(f"📦 Total namespaces: {len(items)}")
            elif "node" in query.lower():
                st.success(f"🖥 Total nodes: {len(items)}")
            elif "pod" in query.lower():
                st.success(f"🐳 Total pods: {len(items)}")
        except Exception:
            pass
