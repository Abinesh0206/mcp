import asyncio
import json
import streamlit as st
import requests
from mcp import ClientSession
from mcp.transport.http import HTTPClientTransport

# ---------------- CONFIG ----------------
MCP_SERVER_URL = "http://18.234.91.216:3000/mcp"
GEMINI_API_KEY = "AIzaSyA-iOGmYUxW000Nk6ORFFopi3cJE7J8wA4"
GEMINI_MODEL = "gemini-1.5-flash"

# Gemini API
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"

# ---------------- GEMINI FUNCTION ----------------
def ask_gemini(prompt: str):
    try:
        headers = {"Content-Type": "application/json"}
        data = {"contents": [{"parts": [{"text": prompt}]}]}
        response = requests.post(GEMINI_URL, headers=headers, json=data, timeout=30)
        response.raise_for_status()
        result = response.json()
        return result["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        return f"❌ Gemini Error: {str(e)}"

# ---------------- MCP QUERY ----------------
async def query_mcp(method: str, params: dict = None):
    """
    Query MCP server using official transport (not plain HTTP POST).
    """
    try:
        async with HTTPClientTransport(MCP_SERVER_URL) as transport:
            async with ClientSession(transport) as session:
                response = await session.send(method, params or {})
                return response
    except Exception as e:
        return {"error": str(e)}

# ---------------- STREAMLIT APP ----------------
st.set_page_config(page_title="MasaBot – MCP + Gemini", layout="wide")
st.title("🤖 MasaBot – MCP + Gemini Chatbot")
st.caption(f"🔗 Connected to MCP server: `{MCP_SERVER_URL}`")

if "messages" not in st.session_state:
    st.session_state.messages = []

# Show history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if prompt := st.chat_input("Ask something (Kubernetes / General)..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Decide which method to call
    if "namespace" in prompt.lower():
        mcp_response = asyncio.run(query_mcp("kubectlGet", {"resource": "namespaces"}))
    elif "pod" in prompt.lower():
        mcp_response = asyncio.run(query_mcp("kubectlGet", {"resource": "pods"}))
    else:
        mcp_response = asyncio.run(query_mcp("listTools"))

    # Ask Gemini
    gemini_prompt = (
        f"User asked: {prompt}\n\n"
        f"MCP Server Raw Response:\n{json.dumps(mcp_response, indent=2)}\n\n"
        "Explain this response in simple terms."
    )
    gemini_answer = ask_gemini(gemini_prompt)

    # Show assistant response
    response_text = (
        f"📡 **MCP Response:**\n```json\n{json.dumps(mcp_response, indent=2)}\n```\n\n"
        f"🤖 **Gemini:** {gemini_answer}"
    )
    st.session_state.messages.append({"role": "assistant", "content": response_text})
    with st.chat_message("assistant"):
        st.markdown(response_text)
