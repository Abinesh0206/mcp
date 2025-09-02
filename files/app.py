import streamlit as st
import requests
import json
import re
import os

# ---------------- CONFIG ----------------
BASE_DIR = os.path.dirname(__files__)  # directory of this script
CONFIG_FILE = os.path.join(BASE_DIR, "servers.json")  # absolute path to servers.json

GEMINI_API_KEY = "AIzaSyA-iOGmYUxW000Nk6ORFFopi3cJE7J8wA4"
GEMINI_MODEL = "gemini-1.5-flash"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"

# ---------------- LOAD SERVER CONFIG ----------------
with open(CONFIG_FILE, "r") as f:
    CONFIG = json.load(f)

SERVERS = {srv["name"]: srv for srv in CONFIG["servers"]}
ROUTING = CONFIG["routing"]

# ---------------- FUNCTIONS ----------------
def route_server(prompt: str):
    """Match prompt with regex and pick the right server."""
    for rule in ROUTING:
        if re.search(rule["matcher"], prompt, re.IGNORECASE):
            return SERVERS[rule["server"]]
    return None  # default → no match

def query_mcp_server(server: dict, method: str, params: dict = None):
    """
    Sends JSON-RPC request to MCP server.
    """
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": "1",
            "method": method,
            "params": params or {}
        }
        headers = {"Content-Type": "application/json"}
        if "authHeader" in server:
            # expand environment variable tokens if needed
            headers["Authorization"] = server["authHeader"].replace("${", "").replace("}", "")
        response = requests.post(f"{server['baseUrl']}/mcp", headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {"error": str(e)}

def ask_gemini(prompt: str):
    """Send text to Gemini API for interpretation."""
    try:
        headers = {"Content-Type": "application/json"}
        data = {"contents": [{"parts": [{"text": prompt}]}]}
        response = requests.post(GEMINI_URL, headers=headers, json=data, timeout=30)
        response.raise_for_status()
        result = response.json()
        return result["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        return f"❌ Gemini Error: {str(e)}"

# ---------------- STREAMLIT UI ----------------
st.set_page_config(page_title="MasaBot – MCP + Gemini", layout="wide")
st.title("🤖 MasaBot – MCP + Gemini Chatbot")
st.caption("🔗 Multi-Server MCP Client with Gemini")

if "messages" not in st.session_state:
    st.session_state.messages = []

# Show chat history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Input box
if prompt := st.chat_input("Ask something (Kubernetes / Jenkins / ArgoCD)..."):
    # Save user message
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Decide server based on routing rules
    server = route_server(prompt)
    if server:
        # Example default method handling
        if "namespace" in prompt.lower():
            mcp_response = query_mcp_server(server, "kubectlGet", {"resource": "namespaces"})
        elif "pod" in prompt.lower():
            mcp_response = query_mcp_server(server, "kubectlGet", {"resource": "pods"})
        else:
            mcp_response = query_mcp_server(server, "listTools")
    else:
        mcp_response = {"error": "No matching server found for this query."}

    # Ask Gemini to explain
    gemini_prompt = (
        f"User asked: {prompt}\n\n"
        f"MCP Server Raw Response:\n{json.dumps(mcp_response, indent=2)}\n\n"
        "Explain this response in simple terms."
    )
    gemini_answer = ask_gemini(gemini_prompt)

    # Final response
    response_text = (
        f"📡 **MCP Response from {server['name'] if server else 'Unknown'}:**\n"
        f"```json\n{json.dumps(mcp_response, indent=2)}\n```\n\n"
        f"🤖 **Gemini:** {gemini_answer}"
    )

    st.session_state.messages.append({"role": "assistant", "content": response_text})
    with st.chat_message("assistant"):
        st.markdown(response_text)
