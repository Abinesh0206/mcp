# app.py
import os
import json
import requests
import streamlit as st
from dotenv import load_dotenv
import google.generativeai as genai
from datetime import datetime, timezone

# ---------------- CONFIG ----------------
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyBYRBa7dQ5atjlHk7e3IOdZBdo6OOcn2Pk")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

# Configure Gemini SDK
GEMINI_AVAILABLE = False
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        GEMINI_AVAILABLE = True
    except Exception:
        GEMINI_AVAILABLE = False


# ---------------- SERVERS ----------------
def load_servers():
    try:
        with open("servers.json") as f:
            data = json.load(f)
        return data.get("servers", []) or []
    except Exception:
        return [
            {"name": "kubernetes-mcp", "url": "http://127.0.0.1:3001/mcp", "description": "Kubernetes MCP"},
            {"name": "argocd-mcp", "url": "http://127.0.0.1:3002/mcp", "description": "ArgoCD MCP"},
            {"name": "jenkins-mcp", "url": "http://127.0.0.1:3003/mcp", "description": "Jenkins MCP"},
        ]

servers = load_servers()
server_map = {s["name"]: s["url"] for s in servers}

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
            headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
            json=payload,
            timeout=30,
        )
        res.raise_for_status()
        text = res.text.strip()
        if text.startswith("event:") or "data:" in text:
            for line in text.splitlines():
                if line.startswith("data:"):
                    payload_text = line[len("data:"):].strip()
                    try:
                        return json.loads(payload_text)
                    except Exception:
                        return {"result": payload_text}
        try:
            return res.json()
        except ValueError:
            return {"result": res.text}
    except requests.exceptions.RequestException as e:
        return {"error": f"MCP server request failed: {str(e)}"}


def list_mcp_tools(server_url: str):
    """Fetch available tools from a specific MCP server."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    try:
        res = requests.post(
            server_url,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            json=payload,
            timeout=15,
        )
        res.raise_for_status()
        resp = res.json()
        result = resp.get("result")
        if isinstance(result, dict):
            return result.get("tools", [])
        if isinstance(result, list):
            return result
        return []
    except Exception:
        return []


def call_tool(name: str, arguments: dict):
    return call_mcp_server("tools/call", {"name": name, "arguments": arguments})


def ask_gemini(prompt: str):
    if not GEMINI_AVAILABLE:
        return "Gemini not configured."
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(prompt)
        return response.text if hasattr(response, "text") else str(response)
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


# ---------------- GEMINI DECISION HELPERS ----------------
def ask_gemini_for_server_and_tool(query: str):
    """Step 1: decide server. Step 2: choose valid tool from that server."""
    server_instruction = f"""
User query: "{query}"

Choose the correct MCP server:
- kubernetes-mcp → for Kubernetes cluster operations (pods, nodes, services, namespaces)
- argocd-mcp → for ArgoCD operations (applications, projects, sync, deployments)
- jenkins-mcp → for Jenkins operations (jobs, builds, pipelines, plugins)

Respond JSON only:
{{"server": "kubernetes-mcp" | "argocd-mcp" | "jenkins-mcp" | null, "explanation": "short reasoning"}}
"""
    if not GEMINI_AVAILABLE:
        return {"server": None, "tool": None, "args": None, "explanation": "Gemini not available"}

    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(server_instruction)
        text = response.text.strip()
        parsed = json.loads(text[text.find("{"): text.rfind("}")+1])
        server = parsed.get("server")
        explanation = parsed.get("explanation", "")

        if not server or server not in server_map:
            return {"server": None, "tool": None, "args": None, "explanation": explanation}

        # Step 2: fetch tools from that server
        tools = list_mcp_tools(server_map[server])
        tool_names = [t["name"] for t in tools]

        tool_instruction = f"""
User query: "{query}"
Server chosen: {server}
Available tools: {json.dumps(tool_names, indent=2)}

Pick the best tool and required args.
Strict JSON only:
{{"tool": "<tool_name>" | null, "args": {{}} | null, "explanation": "short reasoning"}}
"""
        response2 = model.generate_content(tool_instruction)
        text2 = response2.text.strip()
        parsed2 = json.loads(text2[text2.find("{"): text2.rfind("}")+1])

        return {
            "server": server,
            "tool": parsed2.get("tool"),
            "args": sanitize_args(parsed2.get("args")),
            "explanation": explanation + " → " + parsed2.get("explanation", "")
        }
    except Exception as e:
        return {"server": None, "tool": None, "args": None, "explanation": f"Gemini error: {str(e)}"}


# ---------------- STREAMLIT APP ----------------
def main():
    st.set_page_config(page_title="MCP Chat Assistant", page_icon="⚡", layout="wide")
    st.title("🤖 Masa Bot Assistant")

    st.sidebar.subheader("🌐 Available MCP Servers")
    for s in servers:
        st.sidebar.write(f"- **{s['name']}** → {s['url']}")

    if "messages" not in st.session_state:
        st.session_state["messages"] = []

    for msg in st.session_state["messages"]:
        with st.chat_message(msg.get("role", "assistant")):
            st.markdown(msg.get("content", ""))

    with st.form("user_input_form", clear_on_submit=True):
        user_input = st.text_input("Ask anything (Kubernetes / ArgoCD / Jenkins)...")
        submitted = st.form_submit_button("Send")

    if submitted and user_input:
        st.session_state["messages"].append({"role": "user", "content": user_input})
        st.chat_message("user").markdown(user_input)

        decision = ask_gemini_for_server_and_tool(user_input)
        explanation = f"💡 {decision.get('explanation','')}"
        st.session_state["messages"].append({"role": "assistant", "content": explanation})
        st.chat_message("assistant").markdown(explanation)

        server, tool, args = decision.get("server"), decision.get("tool"), decision.get("args")

        if server and tool:
            st.session_state["current_server"] = server_map[server]
            st.chat_message("assistant").markdown(
                f"🌐 Routed to **{server}**\n\n🔧 Executing *{tool}* with arguments:\n```json\n{json.dumps(args, indent=2)}\n```"
            )
            response = call_tool(tool, args or {})
            if GEMINI_AVAILABLE:
                pretty_answer = ask_gemini(
                    f"User asked: {user_input}\n\nRaw MCP response:\n{json.dumps(response, indent=2)}\n\n"
                    "Answer in human-friendly language with bullet points if needed."
                )
                st.session_state["messages"].append({"role": "assistant", "content": pretty_answer})
                st.chat_message("assistant").markdown(pretty_answer)
            else:
                fallback = json.dumps(response, indent=2)
                st.session_state["messages"].append({"role": "assistant", "content": fallback})
                st.chat_message("assistant").markdown(fallback)
        else:
            answer = ask_gemini(user_input) if GEMINI_AVAILABLE else "❌ Could not decide server/tool."
            st.session_state["messages"].append({"role": "assistant", "content": answer})
            st.chat_message("assistant").markdown(answer)


if __name__ == "__main__":
    main()
