# app.py
import os
import json
import requests
import streamlit as st
from dotenv import load_dotenv
import google.generativeai as genai

# ---------------- CONFIG ----------------
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyBlx9uMCC18Uaw4LdhmXmQxsYlpf2DBONo")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

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
        return [{
            "name": "default",
            "url": "http://127.0.0.1:3000/mcp",
            "description": "Fallback server"
        }]

servers = load_servers()

# ---------------- HELPERS ----------------
def call_mcp_server(method: str, params: dict = None, server_url: str = None):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    url = server_url or servers[0]["url"]
    try:
        res = requests.post(
            url,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            json=payload,
            timeout=30,
        )
        res.raise_for_status()
        return res.json()
    except requests.exceptions.RequestException as e:
        return {"error": f"MCP server request failed: {str(e)}"}


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
    """Gemini decides server, tool, args (no explanation returned)."""
    server_tools = {}
    for s in servers:
        tool_list = call_mcp_server("tools/list", server_url=s["url"])
        tools = []
        if isinstance(tool_list, dict):
            result = tool_list.get("result")
            if isinstance(result, dict):
                tools = result.get("tools", [])
            elif isinstance(result, list):
                tools = result
        server_tools[s["name"]] = [t["name"] for t in tools]

    instruction = f"""
User query: "{query}"

Available MCP servers and tools:
{json.dumps(server_tools, indent=2)}

Choose the BEST server and one tool + args.

Respond ONLY in strict JSON:
{{
  "server": "<server_name>" | null,
  "tool": "<tool_name>" | null,
  "args": {{}} | null
}}
"""

    if not GEMINI_AVAILABLE:
        return {"server": None, "tool": None, "args": None}

    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(instruction)
        text = response.text.strip()

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}") + 1
            if start != -1 and end != -1:
                parsed = json.loads(text[start:end])
            else:
                return {"server": None, "tool": None, "args": None}
        parsed["args"] = sanitize_args(parsed.get("args") or {})
        return parsed
    except Exception:
        return {"server": None, "tool": None, "args": None}


def ask_gemini_answer(user_input: str, raw_response: dict):
    """Gemini converts MCP raw response → final human-friendly answer."""
    if not GEMINI_AVAILABLE:
        return json.dumps(raw_response, indent=2)

    model = genai.GenerativeModel(GEMINI_MODEL)
    response = model.generate_content(
        f"User asked: {user_input}\n\n"
        f"Here is the raw MCP response:\n{json.dumps(raw_response, indent=2)}\n\n"
        "Answer clearly in plain English. If there are multiple items, use bullet points. Do not include raw JSON."
    )
    return response.text.strip() if hasattr(response, "text") else str(response)


def ask_gemini(query: str):
    """Fallback: Gemini answers directly without MCP call."""
    if not GEMINI_AVAILABLE:
        return "Gemini not available."
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(query)
        return response.text.strip() if hasattr(response, "text") else str(response)
    except Exception as e:
        return f"Gemini error: {str(e)}"


# ---------------- STREAMLIT APP ----------------
def main():
    st.set_page_config(page_title="MCP Chat Assistant", page_icon="⚡", layout="wide")
    st.title("🤖 Masa Bot Assistant")

    # Sidebar: servers
    st.sidebar.subheader("🌐 MCP Servers")
    for s in servers:
        health = call_mcp_server("tools/list", server_url=s["url"])
        status_icon = "✅" if "result" in str(health) else "❌"
        st.sidebar.markdown(f"- {s['name']} {status_icon}")

    # Chat history
    if "messages" not in st.session_state:
        st.session_state["messages"] = []

    for msg in st.session_state["messages"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Chat input
    if prompt := st.chat_input("Ask Kubernetes, ArgoCD, or Jenkins something..."):
        st.session_state["messages"].append({"role": "user", "content": prompt})
        st.chat_message("user").markdown(prompt)

        decision = ask_gemini_for_tool_decision(prompt)
        if decision.get("server") and decision.get("tool"):
            server = next((s for s in servers if s["name"] == decision["server"]), servers[0])
            response = call_mcp_server(
                "tools/call",
                {"name": decision["tool"], "arguments": decision["args"]},
                server_url=server["url"],
            )
            final_answer = ask_gemini_answer(prompt, response)
        else:
            # NEW: fallback Gemini-only answer if no tool chosen
            final_answer = ask_gemini(prompt)

        st.session_state["messages"].append({"role": "assistant", "content": final_answer})
        st.chat_message("assistant").markdown(final_answer)


if __name__ == "__main__":
    main()
