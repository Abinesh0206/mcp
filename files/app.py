import os
import json
import requests
import streamlit as st
import pandas as pd
from dotenv import load_dotenv
import google.generativeai as genai

# ---------------- CONFIG ----------------
load_dotenv()

MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://13.221.252.52:3000/mcp")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyDHN1tGJLFojK65QgcxnZm8QApZdXSDl1w")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

# Configure Gemini
genai.configure(api_key=GEMINI_API_KEY)

# ---------------- HELPERS ----------------
def call_mcp_server(method: str, params: dict = None):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    try:
        res = requests.post(
            MCP_SERVER_URL,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json=payload,
            timeout=30,
        )
        res.raise_for_status()

        text = res.text.strip()
        if text.startswith("event:"):
            for line in text.splitlines():
                if line.startswith("data:"):
                    return json.loads(line[len("data:"):].strip())
        return res.json()
    except requests.exceptions.RequestException as e:
        return {"error": f"MCP server request failed: {str(e)}"}


def list_mcp_tools():
    resp = call_mcp_server("tools/list")
    if "result" in resp and isinstance(resp["result"], dict):
        return resp["result"].get("tools", [])
    return []


def call_tool(name: str, arguments: dict):
    if not name or not isinstance(arguments, dict):
        return {"error": "Invalid tool name or arguments"}
    return call_mcp_server("tools/call", {"name": name, "arguments": arguments})


def render_mcp_response(response: dict):
    """Pretty-print MCP server response like ChatGPT."""
    if "error" in response:
        st.chat_message("assistant").error(f"❌ Error: {response['error']}")
        return

    result = response.get("result", {})

    # Case 1: Text response (describe, helm output, etc.)
    content = result.get("content", [])
    if isinstance(content, list) and len(content) > 0:
        text_blocks = [c.get("text", "") for c in content if c.get("type") == "text"]
        if text_blocks:
            st.chat_message("assistant").code("\n".join(text_blocks).strip(), language="yaml")
            return

    # Case 2: Items list (namespaces, pods, deployments, etc.)
    if "items" in result:
        items = result.get("items", [])
        if not items:
            st.chat_message("assistant").info("ℹ️ No resources found in the cluster.")
            return

        df = pd.DataFrame(items)
        useful_cols = [col for col in ["name", "namespace", "kind", "status", "createdAt"] if col in df.columns]
        if useful_cols:
            df = df[useful_cols]
        st.chat_message("assistant").dataframe(df, use_container_width=True)
        return

    # Case 3: Fallback
    st.chat_message("assistant").warning("⚠️ No usable response from MCP server.")


def ask_gemini(prompt: str):
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Gemini error: {str(e)}"


def sanitize_args(args: dict):
    if not args:
        return {}
    fixed = args.copy()

    if "resource" in fixed and "resourceType" not in fixed:
        fixed["resourceType"] = fixed.pop("resource")

    if fixed.get("resourceType") == "pods" and "namespace" not in fixed:
        fixed["namespace"] = "default"

    if fixed.get("namespace") == "all":
        fixed["allNamespaces"] = True
        fixed.pop("namespace", None)

    return fixed


def ask_gemini_for_tool_decision(query: str):
    instruction = f"""
You are an AI agent that decides if a user query requires calling a Kubernetes MCP tool.

Query: "{query}"

Respond ONLY in strict JSON:
{{
  "tool": "kubectl_get" | "kubectl_create" | "kubectl_delete" | "kubectl_describe" | "install_helm_chart" | null,
  "args": {{}} or null,
  "explanation": "Short explanation"
}}
"""
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(instruction)
        text = response.text.strip()

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start != -1 and end != -1:
                parsed = json.loads(text[start:end])
            else:
                return {"tool": None, "args": None, "explanation": f"Gemini invalid: {text}"}

        parsed["args"] = sanitize_args(parsed.get("args"))
        return parsed
    except Exception as e:
        return {"tool": None, "args": None, "explanation": f"Gemini error: {str(e)}"}


# ---------------- STREAMLIT APP ----------------
st.set_page_config(page_title="MCP Chat Assistant", page_icon="⚡", layout="wide")
st.title("🤖 MCP Client – Kubernetes Assistant")

# Init chat history
if "messages" not in st.session_state:
    st.session_state["messages"] = []

# Sidebar tools
tools = list_mcp_tools()
if tools:
    st.sidebar.subheader("🔧 Available MCP Tools")
    for t in tools:
        st.sidebar.write(f"- {t['name']}: {t.get('description', '')}")
else:
    st.sidebar.error("⚠️ Could not fetch tools from MCP server.")

# Render chat history
for msg in st.session_state["messages"]:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Chat input
if prompt := st.chat_input("Ask Kubernetes something..."):
    # Show user message
    st.chat_message("user").markdown(prompt)
    st.session_state["messages"].append({"role": "user", "content": prompt})

    # Decide with Gemini
    decision = ask_gemini_for_tool_decision(prompt)
    st.chat_message("assistant").markdown(f"💡 {decision.get('explanation', '')}")

    if decision["tool"]:
        st.chat_message("assistant").markdown(
            f"🔧 Executing **{decision['tool']}** with arguments:\n```json\n{json.dumps(decision['args'], indent=2)}\n```"
        )
        response = call_tool(decision["tool"], decision["args"])
        render_mcp_response(response)
    else:
        answer = ask_gemini(prompt)
        st.chat_message("assistant").markdown(answer)
