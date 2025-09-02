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
            "id": "1",
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
            "id": "1",
            "method": "kubectl_get",
            "params": {
                "resourceType": "namespaces",
                "allNamespaces": True,
                "output": "json"
            }
        }

    elif "node" in q and "describe" in q:
        return {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "kubectl_describe",
            "params": {
                "resourceType": "nodes",
                "name": "",   # TODO: extract node name if provided
                "namespace": "default"
            }
        }

    elif "node" in q:
        return {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "kubectl_get",
            "params": {
                "resourceType": "nodes",
                "allNamespaces": True,
                "output": "json"
            }
        }

    elif "pod" in q and "logs" in q:
        return {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "kubectl_logs",
            "params": {
                "name": "",   # TODO: extract pod name from query
                "namespace": "default",
                "tailLines": 50
            }
        }

    elif "pod" in q and "describe" in q:
        return {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "kubectl_describe",
            "params": {
                "resourceType": "pods",
                "name": "",   # TODO: extract pod name from query
                "namespace": "default"
            }
        }

    elif "pod" in q:
        return {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "kubectl_get",
            "params": {
                "resourceType": "pods",
                "allNamespaces": True,
                "output": "json"
            }
        }

    return {"jsonrpc": "2.0", "id": "1", "method": "ping", "params": {}}


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
            st.error("⚠️ Gemini error: " + str(g_json))
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
            st.error("⚠️ MCP Server error: " + str(e))
            st.stop()

        # Step 3: Show MCP server response
        st.write("### 📡 MCP Server Response")
        st.json(m_json)

        # Step 4: Show counts if applicable
        if isinstance(m_json, dict) and "result" in m_json:
            try:
                items = m_json["result"].get("items", [])
                if "namespace" in query.lower():
                    st.success(f"📦 Total namespaces: {len(items)}")
                elif "node" in query.lower():
                    st.success(f"🖥️ Total nodes: {len(items)}")
                elif "pod" in query.lower():
                    st.success(f"🐳 Total pods: {len(items)}")
            except Exception:
                pass
