import os
import json
import requests
import streamlit as st
from dotenv import load_dotenv
import google.generativeai as genai
from datetime import datetime, timezone

# ---------------- CONFIG ----------------
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyA-iOGmYUxW000Nk6ORFFopi3cJE7J8wA4")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

genai.configure(api_key=GEMINI_API_KEY)

# ---------------- LOAD SERVERS ----------------
def load_servers():
    try:
        with open("servers.json") as f:
            data = json.load(f)
            return data.get("servers", [])
    except Exception as e:
        return [{"name": "default", "url": "http://127.0.0.1:3000/mcp", "description": f"Fallback server: {e}"}]

servers = load_servers()

# Default to first server
if "current_server" not in st.session_state:
    st.session_state["current_server"] = servers[0]["url"]

def get_current_server_url():
    return st.session_state.get("current_server", servers[0]["url"])

# ---------------- HELPERS ----------------
def call_mcp_server(method: str, params: dict = None):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    try:
        res = requests.post(
            get_current_server_url(),
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
    helm_repos = {
        "harbor": {"repo": "https://helm.goharbor.io", "chart": "harbor"},
        "gitlab": {"repo": "https://charts.gitlab.io", "chart": "gitlab"},
        "sonarqube": {"repo": "https://SonarSource.github.io/helm-chart-sonarqube", "chart": "sonarqube/sonarqube"},
        "prometheus": {"repo": "https://prometheus-community.github.io/helm-charts", "chart": "prometheus"},
        "nginx-ingress": {"repo": "https://kubernetes.github.io/ingress-nginx", "chart": "ingress-nginx"},
    }

    instruction = f"""
You are an AI agent that maps user queries to Kubernetes MCP tools.

User query: "{query}"

Rules:
- "create namespace <name>" -> tool=kubectl_create, args={{"resourceType":"namespace","name":"<name>"}}
- "delete namespace <name>" -> tool=kubectl_delete, args={{"resourceType":"namespace","name":"<name>"}}
- "how many pods in <ns>" -> tool=kubectl_get, args={{"resourceType":"pods","namespace":"<ns>"}}
- "deploy/install official helm chart for <app>" -> 
   tool=install_helm_chart, args={{"repo": "<repo>", "chart": "<chart>", "namespace": "<app>", "createNamespace": true}}

Known Helm repos: {json.dumps(helm_repos, indent=2)}

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
            parsed = json.loads(text[start:end]) if start != -1 and end != -1 else {"tool": None, "args": None, "explanation": f"Gemini invalid: {text}"}
        parsed["args"] = sanitize_args(parsed.get("args"))
        return parsed
    except Exception as e:
        return {"tool": None, "args": None, "explanation": f"Gemini error: {str(e)}"}

# ---------------- STREAMLIT APP ----------------
def main():
    st.set_page_config(page_title="MCP Chat Assistant", page_icon="⚡", layout="wide")
    st.title("🤖 Masa Bot Assistant")

    # Sidebar: select MCP server
    st.sidebar.subheader("🌐 Select MCP Server")
    server_names = [f"{s['name']} ({s['url']})" for s in servers]
    choice = st.sidebar.radio("Available Servers:", server_names)
    st.session_state["current_server"] = next(s["url"] for s in servers if choice.startswith(s["name"]))

    # Show tools for current server
    tools = list_mcp_tools()
    if tools:
        st.sidebar.subheader("🔧 Available MCP Tools")
        for t in tools:
            st.sidebar.write(f"- {t['name']}: {t.get('description','')}")
    else:
        st.sidebar.error("⚠ Could not fetch tools from MCP server.")

    if "messages" not in st.session_state:
        st.session_state["messages"] = []

    # Display chat history
    for msg in st.session_state["messages"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Chat input
    with st.form("user_input_form", clear_on_submit=True):
        user_input = st.text_input("Ask Kubernetes something...")
        submitted = st.form_submit_button("Send")
        if submitted and user_input:
            st.session_state["messages"].append({"role": "user", "content": user_input})
            st.chat_message("user").markdown(user_input)

            decision = ask_gemini_for_tool_decision(user_input)
            explanation = f"💡 {decision.get('explanation','')}"
            st.session_state["messages"].append({"role": "assistant", "content": explanation})
            st.chat_message("assistant").markdown(explanation)

            if decision["tool"]:
                st.chat_message("assistant").markdown(
                    f"🔧 Executing *{decision['tool']}* with arguments:\n```json\n{json.dumps(decision['args'], indent=2)}\n```"
                )
                response = call_tool(decision["tool"], decision["args"])

                if decision["tool"] == "install_helm_chart" and "namespace" in decision["args"]:
                    ns = decision["args"]["namespace"]
                    pods = call_tool("kubectl_get", {"resourceType": "pods", "namespace": ns})
                    response = {"installResponse": response, "pods": pods}

                pretty_answer = ask_gemini(
                    f"User asked: {user_input}\n\n"
                    f"Here is the raw Kubernetes response:\n{json.dumps(response, indent=2)}\n\n"
                    f"Answer in natural human-friendly language. "
                    f"If multiple items (pods, namespaces, services), format as bullet points."
                )

                st.session_state["messages"].append({"role": "assistant", "content":pretty_answer})
                st.chat_message("assistant").markdown(pretty_answer)

            else:
                answer = ask_gemini(user_input)
                st.session_state["messages"].append({"role": "assistant", "content": answer})
                st.chat_message("assistant").markdown(answer)

if __name__ == "__main__":
    main()
