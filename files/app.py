# app.py — SMART ROUTING VERSION

# ================= IMPORTS =================
import os
import json
import time
import requests
import streamlit as st
from dotenv import load_dotenv
from typing import Optional, Dict, Any
import google.generativeai as genai


# ================= CONFIG =================
load_dotenv()

# ✅ USE gemini-2.0-flash-lite
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyD_ZoULiDzQO_ws6GrNvclHyuGbAL1nkIc")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash-lite")

GEMINI_AVAILABLE = False
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model_list = [m.name for m in genai.list_models()]
        if f"models/{GEMINI_MODEL}" in model_list:
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
            {"name": "kubernetes-mcp", "url": "http://127.0.0.1:3000/mcp", "description": "Kubernetes MCP"},
            {"name": "argocd-mcp", "url": "http://127.0.0.1:3001/mcp", "description": "ArgoCD MCP"},
            {"name": "jenkins-mcp", "url": "http://127.0.0.1:3002/mcp", "description": "Jenkins MCP"}
        ]

servers = load_servers()


# ================= HELPERS =================
def call_mcp_server(method: str, params: Optional[Dict[str, Any]] = None, server_url: Optional[str] = None, timeout: int = 20) -> Dict[str, Any]:
    url = server_url or servers[0]["url"]
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream, /"}

    try:
        res = requests.post(url, json=payload, headers=headers, timeout=timeout)
        res.raise_for_status()
        text = res.text.strip()

        if "data:" in text:
            for line in text.splitlines():
                if line.startswith("data:"):
                    try:
                        return json.loads(line[5:].strip())
                    except Exception:
                        return {"result": line[5:].strip()}

        try:
            return res.json()
        except ValueError:
            return {"result": res.text}

    except requests.exceptions.RequestException as e:
        return {"error": f"MCP server request failed: {str(e)}"}


def list_mcp_tools(server_url: Optional[str] = None) -> list:
    resp = call_mcp_server("tools/list", server_url=server_url)
    if isinstance(resp, dict):
        result = resp.get("result")
        if isinstance(result, dict):
            return result.get("tools", [])
        if isinstance(result, list):
            return result
    return []


def call_tool(name: str, arguments: dict, server_url: Optional[str] = None) -> Dict[str, Any]:
    return call_mcp_server("tools/call", {"name": name, "arguments": arguments or {}}, server_url=server_url)


# ================= ARG PARSER =================
def sanitize_args(args: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not args:
        return {}
    fixed = dict(args)

    if "resource" in fixed and "resourceType" not in fixed:
        fixed["resourceType"] = fixed.pop("resource")

    return fixed


# ================= SMART ROUTER =================
def ask_gemini_for_tool_and_server(query: str, retries: int = 2) -> Dict[str, Any]:
    query_lower = query.lower()

    # Simple rule-based routing
    if any(word in query_lower for word in ["pod", "namespace", "service", "deployment", "pvc", "node"]):
        return {"tool": "kubectl_get", "args": {"resourceType": "pods", "allNamespaces": True}, "server": "kubernetes-mcp", "explanation": "Querying Kubernetes cluster resources"}
    if "argo" in query_lower or "argocd" in query_lower or "application" in query_lower:
        return {"tool": "list_applications", "args": {}, "server": "argocd-mcp", "explanation": "Querying ArgoCD for applications"}
    if "jenkins" in query_lower or "pipeline" in query_lower or "job" in query_lower or "credential" in query_lower:
        return {"tool": "list_jobs", "args": {}, "server": "jenkins-mcp", "explanation": "Querying Jenkins for jobs/credentials"}

    # If Gemini not available, fallback
    if not GEMINI_AVAILABLE:
        return {"tool": None, "args": None, "server": None, "explanation": "⚠ No Gemini, try simple query."}

    # Use Gemini AI for flexible queries
    tool_names = [t.get("name") for s in servers for t in list_mcp_tools(s["url"]) if isinstance(t, dict)]
    server_names = [s["name"] for s in servers]

    instruction = f"""
You are an AI router. 
User: "{query}"
Servers: {json.dumps(server_names)}
Tools: {json.dumps(tool_names)}

Decide the best <tool> and <server>. 
Return strict JSON only:
{{"tool": "<tool_name>", "args": {{"resourceType": "..."}}, "server": "<server_name>", "explanation": "short reason"}}
"""

    for _ in range(retries):
        try:
            model = genai.GenerativeModel(GEMINI_MODEL)
            resp = model.generate_content(instruction, generation_config={"temperature": 0.0})
            text = getattr(resp, "text", str(resp)).strip()

            if "json" in text:
                text = text.split("json")[1].split("```")[0].strip()

            parsed = json.loads(text)
            parsed["args"] = sanitize_args(parsed.get("args") or {})
            return parsed
        except Exception:
            continue

    return {"tool": None, "args": None, "server": None, "explanation": "⚠ Gemini failed"}


# ================= OUTPUT =================
def ask_gemini_answer(user_input: str, raw_response: dict) -> str:
    try:
        result = raw_response.get("result", raw_response)
        if isinstance(result, list):
            return "\n".join([json.dumps(r) for r in result])
        elif isinstance(result, str):
            return result
        return json.dumps(result, indent=2)
    except Exception:
        return str(raw_response)


def ask_gemini_prettify(user_input: str, response: Any) -> str:
    if not GEMINI_AVAILABLE:
        return ask_gemini_answer(user_input, response)

    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        prompt = (
            f"User asked: {user_input}\n\n"
            f"Raw response:\n{json.dumps(response, indent=2)}\n\n"
            "Convert to clear plain text for admin (no JSON, no markdown)."
        )
        resp = model.generate_content(prompt, generation_config={"temperature": 0.0, "max_output_tokens": 512})
        return getattr(resp, "text", str(resp)).strip()
    except Exception:
        return ask_gemini_answer(user_input, response)


# ================= STREAMLIT APP =================
def main():
    st.set_page_config(page_title="Masa Bot Assistant", page_icon="⚡", layout="wide")
    st.title("🤖 Masa Bot Assistant")

    if "messages" not in st.session_state:
        st.session_state["messages"] = []

    for msg in st.session_state["messages"]:
        with st.chat_message(msg.get("role", "assistant")):
            st.markdown(msg.get("content", ""))

    user_prompt = st.chat_input("Ask Kubernetes, ArgoCD, Jenkins...")
    if not user_prompt:
        return

    st.session_state["messages"].append({"role": "user", "content": user_prompt})
    st.chat_message("user").markdown(user_prompt)

    decision = ask_gemini_for_tool_and_server(user_prompt)
    st.chat_message("assistant").markdown(f"🧠 Routing → {decision.get('server')} → {decision.get('tool')}")

    if not decision.get("tool"):
        answer = "⚠ Could not determine tool. Try again with cluster/argo/jenkins query."
        st.session_state["messages"].append({"role": "assistant", "content": answer})
        st.chat_message("assistant").markdown(answer)
        return

    server_url = next((s["url"] for s in servers if s["name"] == decision["server"]), servers[0]["url"])
    resp = call_tool(decision["tool"], decision.get("args") or {}, server_url=server_url)

    if not resp or "error" in resp:
        final_answer = f"⚠ Execution failed: {resp.get('error', 'Unknown error')}"
    else:
        final_answer = ask_gemini_prettify(user_prompt, resp)

    st.session_state["messages"].append({"role": "assistant", "content": final_answer})
    st.chat_message("assistant").markdown(final_answer)


if __name__ == "__main__":
    main()
