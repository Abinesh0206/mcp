# app.py — FIXED & ENHANCED VERSION

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
def call_mcp_server(method: str, params: Optional[Dict[str, Any]] = None, server_url: Optional[str] = None, timeout: int = 25) -> Dict[str, Any]:
    url = server_url or servers[0]["url"]
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream, */*"}

    try:
        res = requests.post(url, json=payload, headers=headers, timeout=timeout)
        res.raise_for_status()
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


# ================= SMART ROUTER =================
RESOURCE_KEYWORDS = {
    "pods": ["pod", "pods"],
    "namespaces": ["namespace", "namespaces", "ns"],
    "nodes": ["node", "nodes"],
    "services": ["service", "services", "svc"],
    "deployments": ["deployment", "deployments"],
    "pvc": ["pvc", "persistentvolumeclaim", "volume"],
    "jobs": ["job", "jobs"],
    "applications": ["application", "argo", "argocd"],
}


def map_query_to_resource(query: str) -> list:
    query = query.lower()
    if "all resources" in query or "everything" in query:
        return ["pods", "deployments", "services", "pvc", "nodes", "jobs"]

    matched = []
    for res_type, keywords in RESOURCE_KEYWORDS.items():
        if any(kw in query for kw in keywords):
            matched.append(res_type)

    return matched or ["pods"]


def ask_gemini_for_tool_and_server(query: str, retries: int = 2) -> Dict[str, Any]:
    query_lower = query.lower()

    # Rule-based routing
    if "argo" in query_lower or "argocd" in query_lower:
        return {"tool": "list_applications", "args": {}, "server": "argocd-mcp", "explanation": "Querying ArgoCD applications"}
    if "jenkins" in query_lower or "pipeline" in query_lower or "job" in query_lower:
        return {"tool": "list_jobs", "args": {}, "server": "jenkins-mcp", "explanation": "Querying Jenkins jobs"}

    resources = map_query_to_resource(query_lower)
    return {"tool": "kubectl_get", "args": {"resourceType": ",".join(resources), "allNamespaces": True}, "server": "kubernetes-mcp", "explanation": f"Querying Kubernetes {resources}"}


# ================= OUTPUT =================
def prettify_response(user_input: str, raw_response: dict) -> str:
    if not raw_response:
        return "⚠️ No response received."

    if "error" in raw_response:
        return f"⚠️ {raw_response['error']}"

    result = raw_response.get("result", raw_response)
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        return "\n".join([json.dumps(r, indent=2) for r in result])
    if isinstance(result, dict):
        return json.dumps(result, indent=2)

    return str(result)


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
    st.chat_message("assistant").markdown(f"🧠 Routing → {decision.get('server')} → {decision.get('tool')} with args {decision.get('args')}")

    server_url = next((s["url"] for s in servers if s["name"] == decision["server"]), servers[0]["url"])
    resp = call_tool(decision["tool"], decision.get("args") or {}, server_url=server_url)

    final_answer = prettify_response(user_prompt, resp)

    st.session_state["messages"].append({"role": "assistant", "content": final_answer})
    st.chat_message("assistant").markdown(final_answer)


if __name__ == "__main__":
    main()
