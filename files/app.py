import os
import json
import requests
import streamlit as st
from dotenv import load_dotenv
import google.generativeai as genai
from datetime import datetime, timezone

# ---------------- CONFIG ----------------
load_dotenv()
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://13.221.252.52:3000/mcp")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyC7iRO4NnyQz144aEc6RiVUNzjL9C051V8")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

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

def humanize_age(created_at: str) -> str:
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - created
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return f"{seconds}s"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes}m"
        hours = minutes // 60
        if hours < 24:
            return f"{hours}h{minutes%60}m"
        days = hours // 24
        hours = hours % 24
        return f"{days}d{hours}h"
    except Exception:
        return "-"

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

If the query mentions installing or deploying an "official Helm chart" (like Harbor, GitLab, Prometheus, etc.),
map it to the **install_helm_chart** tool and return the correct repository and chart name.

Examples:
- "deploy official helm chart for harbor" -> tool=install_helm_chart, args={{"repo":"https://helm.goharbor.io","chart":"harbor"}}
- "install gitlab helm chart" -> tool=install_helm_chart, args={{"repo":"https://charts.gitlab.io","chart":"gitlab"}}

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
            parsed = json.loads(text[start:end]) if start!=-1 and end!=-1 else {"tool":None,"args":None,"explanation":f"Gemini invalid: {text}"}
        parsed["args"] = sanitize_args(parsed.get("args"))
        return parsed
    except Exception as e:
        return {"tool": None, "args": None, "explanation": f"Gemini error: {str(e)}"}

# ---------------- STREAMLIT APP ----------------
def main():
    st.set_page_config(page_title="MCP Chat Assistant", page_icon="⚡", layout="wide")
    st.title("🤖 MCP Client – Kubernetes Assistant")

    if "messages" not in st.session_state:
        st.session_state["messages"] = []

    # Sidebar tools
    tools = list_mcp_tools()
    if tools:
        st.sidebar.subheader("🔧 Available MCP Tools")
        for t in tools:
            st.sidebar.write(f"- {t['name']}: {t.get('description','')}")
    else:
        st.sidebar.error("⚠️ Could not fetch tools from MCP server.")

    # Display chat history
    for msg in st.session_state["messages"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Chat input
    with st.form("user_input_form", clear_on_submit=True):
        user_input = st.text_input("Ask Kubernetes something...")
        submitted = st.form_submit_button("Send")
        if submitted and user_input:
            st.session_state["messages"].append({"role":"user","content":user_input})
            st.chat_message("user").markdown(user_input)

            decision = ask_gemini_for_tool_decision(user_input)
            explanation = f"💡 {decision.get('explanation','')}"
            st.session_state["messages"].append({"role":"assistant","content":explanation})
            st.chat_message("assistant").markdown(explanation)

            if decision["tool"]:
                st.chat_message("assistant").markdown(
                    f"🔧 Executing **{decision['tool']}** with arguments:\n```json\n{json.dumps(decision['args'], indent=2)}\n```"
                )
                response = call_tool(decision["tool"], decision["args"])

                # 🔥 Convert raw JSON into natural language with bullet formatting
                pretty_answer = ask_gemini(
                    f"User asked: {user_input}\n\n"
                    f"Here is the raw Kubernetes response:\n{json.dumps(response, indent=2)}\n\n"
                    f"Answer in natural human-friendly language. "
                    f"If the response contains multiple items (like namespaces, pods, services), "
                    f"list them clearly as bullet points, one per line."
                )

                st.session_state["messages"].append({"role":"assistant","content":pretty_answer})
                st.chat_message("assistant").markdown(pretty_answer)

            else:
                answer = ask_gemini(user_input)
                st.session_state["messages"].append({"role":"assistant","content":answer})
                st.chat_message("assistant").markdown(answer)

if __name__ == "__main__":
    main()
