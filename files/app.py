import streamlit as st
import requests
import json

# ---------------- CONFIG ----------------
MCP_SERVER_URL = "http://18.234.91.216:3000"   # your MCP server
GEMINI_API_KEY = "AIzaSyA-iOGmYUxW000Nk6ORFFopi3cJE7J8wA4"
GEMINI_MODEL = "gemini-1.5-flash"

# Google Gemini API endpoint
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"

# ---------------- FUNCTIONS ----------------
def query_mcp_server(method: str, params: dict = None):
    """
    Sends a JSON-RPC request to the MCP server.
    """
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": "1",
            "method": method,
            "params": params or {}
        }
        headers = {"Content-Type": "application/json"}
        response = requests.post(MCP_SERVER_URL, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {"error": str(e)}

def ask_gemini(prompt: str):
    """
    Sends the query/response to Gemini for interpretation.
    """
    try:
        headers = {"Content-Type": "application/json"}
        data = {
            "contents": [
                {"parts": [{"text": prompt}]}
            ]
        }
        response = requests.post(GEMINI_URL, headers=headers, json=data, timeout=30)
        response.raise_for_status()
        result = response.json()
        return result["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        return f"❌ Gemini Error: {str(e)}"

# ---------------- STREAMLIT UI ----------------
st.set_page_config(page_title="MasaBot – MCP + Gemini", layout="wide")
st.title("🤖 MasaBot – MCP + Gemini Chatbot")
st.caption(f"🔗 Connected to MCP server: `{MCP_SERVER_URL}`")

if "messages" not in st.session_state:
    st.session_state.messages = []

# Show chat history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Input box
if prompt := st.chat_input("Ask something (Kubernetes / General)..."):
    # Save user message
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Example: if user asks about namespaces, call kubectlGet
    if "namespace" in prompt.lower():
        mcp_response = query_mcp_server("kubectlGet", {"resource": "namespaces"})
    else:
        # default: just list tools so user can explore
        mcp_response = query_mcp_server("listTools")

    # Ask Gemini to explain
    gemini_prompt = (
        f"User asked: {prompt}\n\n"
        f"MCP Server Raw Response:\n{json.dumps(mcp_response, indent=2)}\n\n"
        "Explain this response in simple terms."
    )
    gemini_answer = ask_gemini(gemini_prompt)

    # Build final response
    response_text = (
        f"📡 **MCP Response:**\n```json\n{json.dumps(mcp_response, indent=2)}\n```\n\n"
        f"🤖 **Gemini:** {gemini_answer}"
    )

    st.session_state.messages.append({"role": "assistant", "content": response_text})
    with st.chat_message("assistant"):
        st.markdown(response_text)
