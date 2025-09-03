import os
import json
import requests
import streamlit as st
from dotenv import load_dotenv
import google.generativeai as genai

# ---------------- CONFIG ----------------
load_dotenv()

# Environment variables (with defaults if missing)
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://18.234.91.216:3000/mcp")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyA-iOGmYUxW000Nk6ORFFopi3cJE7J8wA4")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

# Gemini config
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(GEMINI_MODEL)

# ---------------- MCP CALL ----------------
def call_mcp(method, params=None):
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or {},
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    resp = requests.post(MCP_SERVER_URL, headers=headers, json=payload)
    text = resp.text

    # Extract "data: {}"
    for line in text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    raise Exception("Invalid MCP response: " + text)

# ---------------- ASK CLUSTER ----------------
def ask_cluster(question):
    mapping_prompt = f"""
    Convert this question into a valid MCP call JSON ONLY.
    Do not include code fences, markdown, or explanations.
    Available method: "kubectl_get" with params {{resourceType, namespace?}}.

    Example:
    {{"method": "kubectl_get", "params": {{"resourceType": "pods"}}}}

    Q: "{question}"
    """
    mapping_resp = model.generate_content(mapping_prompt)
    mapping_text = mapping_resp.text.strip()

    # Clean output (remove ```json ... ```)
    if mapping_text.startswith("```"):
        mapping_text = mapping_text.strip("`").replace("json", "", 1).strip()

    try:
        mapping = json.loads(mapping_text)
    except Exception:
        return f"⚠ Could not map your question to an MCP call. Gemini said: {mapping_text}"

    # Call MCP
    try:
        mcp_resp = call_mcp(mapping["method"], mapping.get("params", {}))
    except Exception as e:
        return f"⚠ MCP call failed: {str(e)}"

    # Summarize result
    summary_prompt = f"""
    Summarize this JSON as a human-readable answer:
    Q: {question}
    JSON: {json.dumps(mcp_resp.get("result", mcp_resp))}
    """
    summary_resp = model.generate_content(summary_prompt)
    return summary_resp.text

# ---------------- STREAMLIT UI ----------------
st.set_page_config(page_title="K8s Chat", page_icon="☁", layout="wide")

st.title("☁ Kubernetes Chat Assistant")

if "history" not in st.session_state:
    st.session_state.history = []

# Chat UI
for msg in st.session_state.history:
    role, text = msg
    with st.chat_message(role):
        st.markdown(text)

# Input box
if question := st.chat_input("Ask about your cluster..."):
    st.session_state.history.append(("user", question))
    with st.chat_message("user"):
        st.markdown(question)

    try:
        answer = ask_cluster(question)
    except Exception as e:
        answer = f"⚠ {str(e)}"

    st.session_state.history.append(("assistant", answer))
    with st.chat_message("assistant"):
        st.markdown(answer)
