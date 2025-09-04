import os
import json
import requests
import streamlit as st
from dotenv import load_dotenv
import google.generativeai as genai

# ---------------- CONFIG ----------------
load_dotenv()

MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:3000/mcp")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyA-iOGmYUxW000Nk6ORFFopi3cJE7J8wA4")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
K8S_MCP_TOKEN = os.getenv("K8S_MCP_TOKEN", "")

# Configure Gemini
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(GEMINI_MODEL)
else:
    model = None

# ---------------- JSON-RPC HELPER ----------------
def call_mcp(method, params=None):
    """
    Send a JSON-RPC request to the MCP server via HTTP POST (/mcp).
    """
    headers = {"Content-Type": "application/json"}
    if K8S_MCP_TOKEN:
        headers["Authorization"] = f"Bearer {K8S_MCP_TOKEN}"

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or {},
    }

    resp = requests.post(MCP_SERVER_URL, headers=headers, json=payload)
    resp.raise_for_status()
    return resp.json()

def list_tools():
    """Get available MCP tools."""
    return call_mcp("rpc.discover")

# ---------------- NATURAL QUESTION HANDLER ----------------
def ask_cluster(question: str):
    q = question.lower().strip()

    if "namespaces" in q:
        return call_mcp("tools.call", {
            "name": "namespace_list",
            "arguments": {}
        })

    elif "pods" in q and "all" in q:
        return call_mcp("tools.call", {
            "name": "kubectl_get",
            "arguments": {"resource": "pods", "namespace": ""}
        })

    elif "pods" in q:
        return call_mcp("tools.call", {
            "name": "kubectl_get",
            "arguments": {"resource": "pods", "namespace": "default"}
        })

    elif "list tools" in q or "methods" in q:
        return list_tools()

    else:
        return {"error": "I don't know how to answer that yet."}

# ---------------- STREAMLIT UI ----------------
st.set_page_config(page_title="MCP Client", page_icon="🤖")
st.title("🤖 MCP Client – K8s Assistant")

if "messages" not in st.session_state:
    st.session_state.messages = []

# Chat display
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Chat input
if prompt := st.chat_input("Ask about your Kubernetes cluster..."):
    st.session_state.messages.append({"role": "user", "content": prompt})

    with st.chat_message("user"):
        st.markdown(prompt)

    try:
        raw_response = ask_cluster(prompt)
        pretty_response = json.dumps(raw_response, indent=2)

        # Optional: natural explanation with Gemini
        if model:
            gemini_response = model.generate_content(
                f"User asked: {prompt}\n\nMCP raw response:\n{pretty_response}\n\nExplain clearly:"
            )
            answer = gemini_response.text
        else:
            answer = pretty_response

    except Exception as e:
        answer = f"⚠️ Error: {str(e)}"

    with st.chat_message("assistant"):
        st.markdown(answer)

    st.session_state.messages.append({"role": "assistant", "content": answer})
