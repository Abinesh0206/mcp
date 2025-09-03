import os
import json
import requests
import streamlit as st
from dotenv import load_dotenv
import google.generativeai as genai

# ---------------- CONFIG ----------------
load_dotenv()

MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://18.234.91.216:3000/mcp")
K8S_MCP_TOKEN = eyJhbGciOiJSUzI1NiIsImtpZCI6IkJ3Q1hSd0FpUVRCeUM4RDdXUmpyOVJ3N3MzVUlOUzNJSnloV25rYktMUVkifQ.eyJpc3MiOiJrdWJlcm5ldGVzL3NlcnZpY2VhY2NvdW50Iiwia3ViZXJuZXRlcy5pby9zZXJ2aWNlYWNjb3VudC9uYW1lc3BhY2UiOiJkZWZhdWx0Iiwia3ViZXJuZXRlcy5pby9zZXJ2aWNlYWNjb3VudC9zZWNyZXQubmFtZSI6Im1jcC1rdWJlLXRva2VuIiwia3ViZXJuZXRlcy5pby9zZXJ2aWNlYWNjb3VudC9zZXJ2aWNlLWFjY291bnQubmFtZSI6Im1jcC1rdWJlIiwia3ViZXJuZXRlcy5pby9zZXJ2aWNlYWNjb3VudC9zZXJ2aWNlLWFjY291bnQudWlkIjoiYWM5ZjZhNGEtZGQ2YS00M2MyLWE5YTgtM2MyOWFjZWQwN2IxIiwic3ViIjoic3lzdGVtOnNlcnZpY2VhY2NvdW50OmRlZmF1bHQ6bWNwLWt1YmUifQ.itEYExlQrjIPzwmRFTaly1Jmmrg5jGRq_OoEp0ALQEK1LutRRAQZIfuTgx4OnQR4yj2K79idjpgAV2m-5zrvSWmKixFf2Uo9OR3Z8DSLmW51KG2WA3kmhgZYgzMOUrPRHud0gJ7mVgq2yiU9Tbv5yJcWGlZu2YEh-JDFze_ol2qu2C398bj99ubjNLAZ7WAPmAB5gLps3qrMb8Vb5vlnLNwGLfyjN-vFWvPkZUDR2jvKFZIKJHqHO-zWHmomODkAjP4imuVud964uQlqKeevG2Dpb_YwUTH_QX7anYxBcmok2ccFg-4GdQTbhKTcZ3Q4uHGltIT1smyxfWNK4ifxxA
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyA-iOGmYUxW000Nk6ORFFopi3cJE7J8wA4")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

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
        "Authorization": f"Bearer {os.getenv('K8S_MCP_TOKEN')}"  # 🔑 Added
    }
    resp = requests.post(MCP_SERVER_URL, headers=headers, json=payload)

    text = resp.text
    for line in text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    raise Exception("Invalid MCP response: " + text)

# ---------------- DISCOVER TOOLS ----------------
def list_tools():
    try:
        resp = call_mcp("rpc.discover")
        return [t["name"] for t in resp.get("result", {}).get("tools", [])]
    except Exception as e:
        return []

# ---------------- ASK CLUSTER ----------------
def ask_cluster(question):
    q = question.lower().strip()

    # Special case: list tools
    if q in ["list methods", "list method", "methods", "show methods", "list tools"]:
        try:
            mcp_resp = call_mcp("rpc.discover")
            return f"🛠 Available tools:\n```\n{json.dumps(mcp_resp.get('result', mcp_resp), indent=2)}\n```"
        except Exception as e:
            return f"⚠ MCP discover failed: {str(e)}"

    # Discover tools first
    tools = list_tools()
    if not tools:
        return "⚠ No tools discovered from MCP server."

    mapping_prompt = f"""
    You are helping map user questions to MCP server tool calls.

    MCP server supports these tools: {tools}

    Convert this question into a valid JSON ONLY in the following format:
    {{
      "name": "<tool_name>",
      "arguments": {{}}
    }}

    Q: "{question}"
    """
    mapping_resp = model.generate_content(mapping_prompt)
    mapping_text = mapping_resp.text.strip()

    if mapping_text.startswith("```"):
        mapping_text = mapping_text.strip("`").replace("json", "", 1).strip()

    try:
        mapping = json.loads(mapping_text)
    except Exception:
        return f"⚠ Could not map your question to a tool call. Gemini said: {mapping_text}"

    # Call MCP tool
    try:
        mcp_resp = call_mcp("tools/call", {
            "name": mapping["name"],
            "arguments": mapping.get("arguments", {})
        })
    except Exception as e:
        return f"⚠ MCP tool call failed: {mapping['name']}. {str(e)}"

    summary_prompt = f"""
    Summarize this JSON as a human-readable answer:
    Q: {question}
    JSON: {json.dumps(mcp_resp.get("result", mcp_resp))}
    """
    summary_resp = model.generate_content(summary_prompt)
    return summary_resp.text

# ---------------- NORMAL CHAT ----------------
def ask_normal(question):
    resp = model.generate_content(question)
    return resp.text

# ---------------- ROUTER ----------------
def ask(question):
    cluster_keywords = ["kubernetes", "cluster", "pod", "node", "namespace", "service", "deployment"]
    if any(word in question.lower() for word in cluster_keywords):
        return ask_cluster(question)
    return ask_normal(question)

# ---------------- STREAMLIT UI ----------------
st.set_page_config(page_title="K8s Chat", page_icon="☁", layout="wide")
st.title("☁ MASA Bot")

if "history" not in st.session_state:
    st.session_state.history = []

for role, text in st.session_state.history:
    with st.chat_message(role):
        st.markdown(text)

if question := st.chat_input("Ask about your cluster or anything..."):
    st.session_state.history.append(("user", question))
    with st.chat_message("user"):
        st.markdown(question)

    try:
        answer = ask(question)
    except Exception as e:
        answer = f"⚠ {str(e)}"

    st.session_state.history.append(("assistant", answer))
    with st.chat_message("assistant"):
        st.markdown(answer)
