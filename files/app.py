# app.py — FULL GEMINI ROUTER VERSION

# ================= IMPORTS =================
import os
import json
import requests
import streamlit as st
from dotenv import load_dotenv
from typing import Optional, Dict, Any
import google.generativeai as genai


# ================= CONFIG =================
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyBYRBa7dQ5atjlHk7e3IOdZBdo6OOcn2Pk")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash-lite")

GEMINI_AVAILABLE = False
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        GEMINI_AVAILABLE = True
    except Exception as e:
        st.error(f"❌ Gemini setup error: {e}")


# ================= SERVER MANAGEMENT =================
def load_servers() -> list:
    try:
        with open("servers.json") as f:
            data = json.load(f)
            return data.get("servers", []) or []
    except Exception:
        return [
            {"name": "kubernetes-mcp", "url": "http://127.0.0.1:3000/mcp"},
            {"name": "argocd-mcp", "url": "http://127.0.0.1:3001/mcp"},
            {"name": "jenkins-mcp", "url": "http://127.0.0.1:3002/mcp"},
        ]


servers = load_servers()


# ================= MCP HELPER =================
def call_mcp_server(method: str, params: Optional[Dict[str, Any]] = None, server_url: Optional[str] = None) -> Dict[str, Any]:
    url = server_url or servers[0]["url"]
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    headers = {"Content-Type": "application/json"}

    try:
        res = requests.post(url, json=payload, headers=headers, timeout=20)
        res.raise_for_status()
        return res.json()
    except Exception as e:
        return {"error": str(e)}


def list_mcp_tools(server_url: str) -> list:
    resp = call_mcp_server("tools/list", server_url=server_url)
    if isinstance(resp, dict) and "result" in resp:
        tools = resp["result"].get("tools")
        if isinstance(tools, list):
            return tools
    return []


def call_tool(name: str, arguments: dict, server_url: str) -> Dict[str, Any]:
    return call_mcp_server("tools/call", {"name": name, "arguments": arguments or {}}, server_url=server_url)


# ================= ROUTER =================
def ask_gemini_for_tool_and_server(query: str, retries: int = 2) -> Dict[str, Any]:
    """
    Use Gemini to decide which MCP server + tool + args to call.
    No hardcoded routing.
    """
    if not GEMINI_AVAILABLE:
        return {"tool": None, "args": None, "server": None, "explanation": "⚠ Gemini not available."}

    # Collect available servers + tools
    server_names = [s["name"] for s in servers]
    tool_map = {}
    for s in servers:
        tool_map[s["name"]] = [t.get("name") for t in list_mcp_tools(s["url"]) if isinstance(t, dict)]

    instruction = f"""
You are a routing AI.
User asked: "{query}"

Servers: {json.dumps(server_names)}
Tools per server: {json.dumps(tool_map)}

Decide the best server and tool.
Return STRICT JSON only, format:
{{
  "server": "<server_name>",
  "tool": "<tool_name>",
  "args": {{"resourceType": "...", "namespace": "..."}},
  "explanation": "why this server/tool"
}}
"""

    for _ in range(retries):
        try:
            model = genai.GenerativeModel(GEMINI_MODEL)
            resp = model.generate_content(instruction, generation_config={"temperature": 0.0})
            text = getattr(resp, "text", str(resp)).strip()

            # clean ```json ... ```
            if "```" in text:
                text = text.split("```")[1].replace("json", "").strip()

            parsed = json.loads(text)
            return parsed
        except Exception:
            continue

    return {"tool": None, "args": None, "server": None, "explanation": "⚠ Gemini failed to parse"}


# ================= OUTPUT =================
def prettify_answer(user_input: str, response: Any) -> str:
    if not GEMINI_AVAILABLE:
        return json.dumps(response, indent=2)

    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        prompt = (
            f"User asked: {user_input}\n\n"
            f"Raw response:\n{json.dumps(response, indent=2)}\n\n"
            "Rewrite as clear plain text (no JSON, no markdown)."
        )
        resp = model.generate_content(prompt, generation_config={"temperature": 0.0})
        return getattr(resp, "text", str(resp)).strip()
    except Exception:
        return json.dumps(response, indent=2)


# ================= STREAMLIT APP =================
def main():
    st.set_page_config(page_title="Masa Bot Assistant", page_icon="⚡", layout="wide")
    st.title("🤖 Masa Bot Assistant")

    if "messages" not in st.session_state:
        st.session_state["messages"] = []

    for msg in st.session_state["messages"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    user_prompt = st.chat_input("Ask about Kubernetes, ArgoCD, Jenkins...")
    if not user_prompt:
        return

    # Show user input
    st.session_state["messages"].append({"role": "user", "content": user_prompt})
    st.chat_message("user").markdown(user_prompt)

    # Routing via Gemini
    decision = ask_gemini_for_tool_and_server(user_prompt)
    st.chat_message("assistant").markdown(f"🧠 Routing → {decision.get('server')} → {decision.get('tool')}")

    if not decision.get("tool") or not decision.get("server"):
        answer = "⚠ Could not determine correct tool/server."
        st.session_state["messages"].append({"role": "assistant", "content": answer})
        st.chat_message("assistant").markdown(answer)
        return

    server_url = next((s["url"] for s in servers if s["name"] == decision["server"]), servers[0]["url"])
    resp = call_tool(decision["tool"], decision.get("args") or {}, server_url=server_url)

    if not resp or "error" in resp:
        final_answer = f"⚠ Execution failed: {resp.get('error', 'Unknown error')}"
    else:
        final_answer = prettify_answer(user_prompt, resp)

    st.session_state["messages"].append({"role": "assistant", "content": final_answer})
    st.chat_message("assistant").markdown(final_answer)


if __name__ == "__main__":
    main()
