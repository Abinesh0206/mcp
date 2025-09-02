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
    """ Map user query to correct MCP call """
    q = query.lower()

    if "diagnose" in q or "troubleshoot" in q:
        return {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "prompts/get",
            "params": {
                "name": "k8s-diagnose",
                "arguments": {
                    "keyword": "pod",   # TODO: extract dynamically
                    "namespace": "default"
                }
            }
        }

    elif "namespace" in q:
        return {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "kubectl_get",
                "arguments": {
                    "resourceType": "namespaces",
                    "namespace": "default",
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
                    "namespace": "default",
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


def clean_sse_response(raw_text: str):
    """ Extract JSON from SSE stream if server sends 'data:' lines """
    lines = [line for line in raw_text.splitlines() if line.startswith("data:")]
    if not lines:
        return raw_text
    return lines[-1].replace("data:", "").strip()


if st.button("Ask") and query:
    with st.spinner("Gemini thinking..."):

        # Step 1: Ask Gemini to interpret
        gemini_payload = {"contents": [{"parts": [{"text": query}]}]}
        g_res = requests.post(GEMINI_URL, json=gemini_payload)
        g_json = g_res.json()

        try:
            gemini_text = g_json["candidates"][0]["content"]["parts"][0]["text"]
        except Exception:
            st.error("⚠ Gemini error: " + str(g_json))
            st.stop()

        st.write("### 🤖 Gemini Interpretation")
        st.info(gemini_text)

        # Step 2: Build MCP payload
        mcp_payload = build_mcp_payload(query)

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"
        }

        try:
            m_res = requests.post(MCP_SERVER_URL, json=mcp_payload, headers=headers)
            raw_text = m_res.text.strip()
            clean_text = clean_sse_response(raw_text)

            try:
                m_json = json.loads(clean_text)
            except Exception:
                m_json = {"raw_response": raw_text}

        except Exception as e:
            st.error("⚠ MCP Server error: " + str(e))
            st.stop()

        # Step 3: Show MCP server response
        st.write("### 📡 MCP Server Response")
        st.json(m_json)

        # Step 4: Show counts if applicable
        if isinstance(m_json, dict) and "result" in m_json:
            try:
                items = []
                # MCP returns inside result.content[0].text (json string)
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
