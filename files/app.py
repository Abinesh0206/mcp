#!/usr/bin/env python3
# patched_mcp_client.py
import os
import json
import time
import re
from typing import Optional, Dict, Any, List
import requests
import streamlit as st
from dotenv import load_dotenv
from datetime import datetime, timezone

# Optional Gemini SDK
try:
    import google.generativeai as genai
except Exception:
    genai = None

# ---------------- CONFIG ----------------
load_dotenv()
API_URL = os.getenv("API_URL", "http://54.227.78.211:8080")  # Auth gateway URL
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_AVAILABLE = False

if genai and GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        GEMINI_AVAILABLE = True
    except Exception:
        GEMINI_AVAILABLE = False

# ---------------- SERVERS ----------------
def load_servers_from_file() -> List[Dict[str, Any]]:
    try:
        with open("servers.json", "r") as f:
            data = json.load(f)
            servers = data.get("servers") or data.get("Servers") or []
            if isinstance(servers, list) and len(servers) > 0:
                return servers
    except Exception:
        pass
    return [
        {"name": "jenkins", "url": f"{API_URL}/mcp", "description": "Jenkins"},
        {"name": "kubernetes", "url": f"{API_URL}/mcp", "description": "Kubernetes"},
        {"name": "argocd", "url": f"{API_URL}/mcp", "description": "ArgoCD"},
    ]

SERVERS = load_servers_from_file()
SERVER_NAMES = [s.get("name") for s in SERVERS]

# ---------------- HELPERS ----------------
def sanitize_args(args: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not args:
        return {}
    fixed = dict(args)
    if "resource" in fixed and "resourceType" not in fixed:
        fixed["resourceType"] = fixed.pop("resource")
    if fixed.get("namespace") == "all":
        fixed["allNamespaces"] = True
        fixed.pop("namespace", None)
    if fixed.get("resourceType") == "all":
        fixed["allResources"] = True
        fixed.pop("resourceType", None)
    resource_mappings = {
        "ns": "namespaces", "pod": "pods", "node": "nodes", "deploy": "deployments",
        "svc": "services", "cm": "configmaps", "secret": "secrets", "all": "all"
    }
    if fixed.get("resourceType") in resource_mappings:
        fixed["resourceType"] = resource_mappings[fixed["resourceType"]]
    if (fixed.get("resourceType") in ["pods", "services", "deployments", "secrets", "configmaps"]
        and "namespace" not in fixed):
        fixed["allNamespaces"] = True
    return fixed

def _extract_json_from_text(text: str) -> Optional[dict]:
    try:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start:end])
    except Exception:
        pass
    return None

# ---------------- SERVER DETECTION ----------------
def detect_server_from_query(query: str, available_servers: list) -> Optional[Dict[str, Any]]:
    query_lower = query.lower()
    for server in available_servers:
        try:
            tools = list_mcp_tools_for_server(server["name"])
            tool_names = [t.lower() for t in tools]
            for tool_name in tool_names:
                if tool_name in query_lower:
                    return server
            server_name = server["name"].lower()
            if (("kubernetes" in query_lower or "k8s" in query_lower or 
                 "pod" in query_lower or "namespace" in query_lower or
                 "deployment" in query_lower or "service" in query_lower or
                 "secret" in query_lower or "configmap" in query_lower or
                 "node" in query_lower or "cluster" in query_lower or
                 "resource" in query_lower or "create" in query_lower or
                 "delete" in query_lower) and 
                ("kubernetes" in server_name or "k8s" in server_name)):
                return server
            if (("jenkins" in query_lower or "job" in query_lower or 
                 "build" in query_lower or "pipeline" in query_lower) and 
                "jenkins" in server_name):
                return server
            if (("argocd" in query_lower or "application" in query_lower or 
                 "gitops" in query_lower or "sync" in query_lower) and 
                "argocd" in server_name):
                return server
        except Exception:
            continue
    return available_servers[0] if available_servers else None

# ---------------- HTTP helpers ----------------
def build_auth_headers_and_cookies(session_id: Optional[str]) -> Dict[str, Any]:
    headers = {}
    cookies = {}
    if session_id:
        headers["Authorization"] = f"Bearer {session_id}"
        headers["X-Session-Id"] = session_id
        cookies["session_id"] = session_id
    headers["Content-Type"] = "application/json"
    return {"headers": headers, "cookies": cookies}

# ---------------- CALL TOOL ----------------
def call_tool(server_name: str, name: str, arguments: dict, session_id: Optional[str]):
    if not name or not isinstance(arguments, dict):
        return {"error": "Invalid tool name or arguments"}
    rpc_body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
        "session_id": session_id
    }
    url = f"{API_URL}/mcp?target={server_name}"
    auth = build_auth_headers_and_cookies(session_id)
    try:
        resp = requests.post(url, json=rpc_body, headers=auth["headers"], cookies=auth["cookies"], timeout=20)
        if resp.status_code == 401:
            return {"error": f"Gateway returned 401 Unauthorized. Response: {resp.text}"}
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        return {"error": f"Gateway request failed: {str(e)}"}

# ---------------- LIST TOOLS ----------------
def list_mcp_tools_for_server(server_name: str, session_id: Optional[str] = None) -> List[str]:
    rpc_body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {},
        "session_id": session_id
    }
    url = f"{API_URL}/mcp?target={server_name}"
    auth = build_auth_headers_and_cookies(session_id)
    try:
        resp = requests.post(url, json=rpc_body, headers=auth["headers"], cookies=auth["cookies"], timeout=10)
        if resp.status_code == 401:
            return []
        resp.raise_for_status()
        result = resp.json()
        if isinstance(result, dict):
            tools = result.get("result", {}).get("tools") or result.get("result", [])
        elif isinstance(result, list):
            tools = result
        else:
            tools = []
        names = []
        for t in tools:
            if isinstance(t, dict) and t.get("name"):
                names.append(t["name"])
            elif isinstance(t, str):
                names.append(t)
        return names
    except Exception:
        return []

# ---------------- GET ALL RESOURCES ----------------
def get_all_cluster_resources(server_name: str, session_id: Optional[str]):
    resource_types = ["pods", "services", "deployments", "configmaps", "secrets", "namespaces", "nodes"]
    all_resources = {}
    for resource_type in resource_types:
        try:
            params = {"resourceType": resource_type}
            if resource_type not in ["namespaces", "nodes"]:
                params["allNamespaces"] = True
            rpc_body = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "kubectl_get", "arguments": params},
                "session_id": session_id
            }
            url = f"{API_URL}/mcp?target={server_name}"
            auth = build_auth_headers_and_cookies(session_id)
            response = requests.post(url, json=rpc_body, headers=auth["headers"], cookies=auth["cookies"], timeout=30)
            if response.status_code == 401:
                all_resources[resource_type] = "Error: 401 Unauthorized"
                continue
            if response.status_code != 200:
                all_resources[resource_type] = f"Error: HTTP {response.status_code}"
                continue
            result = response.json()
            if isinstance(result, dict) and "result" in result:
                all_resources[resource_type] = result["result"]
            else:
                all_resources[resource_type] = result
            time.sleep(0.05)
        except Exception as e:
            all_resources[resource_type] = f"Exception: {str(e)}"
    return all_resources

# ---------------- GEMINI / Heuristic ----------------
def ask_gemini_for_tool_decision(query: str, server_name: str, retries: int = 2) -> Dict[str, Any]:
    available_tools = list_mcp_tools_for_server(server_name)
    # simplified: if Gemini not available, fallback heuristic
    def heuristic():
        q = query.lower()
        if "create namespace" in q or "create ns" in q:
            match = re.search(r'(?:namespace|ns)[\s]+([\w-]+)', q)
            if match:
                return {"tool":"kubectl_create","args":{"resourceType":"namespaces","name":match.group(1)},"explanation":f"Creating namespace {match.group(1)}"}
        if "delete namespace" in q or "delete ns" in q:
            match = re.search(r'(?:namespace|ns)[\s]+([\w-]+)', q)
            if match:
                return {"tool":"kubectl_delete","args":{"resourceType":"namespaces","name":match.group(1)},"explanation":f"Deleting namespace {match.group(1)}"}
        if "pods" in q:
            return {"tool":"kubectl_get","args":{"resourceType":"pods","allNamespaces":True},"explanation":"Listing all pods"}
        if "services" in q or "svc" in q:
            return {"tool":"kubectl_get","args":{"resourceType":"services","allNamespaces":True},"explanation":"Listing all services"}
        if "nodes" in q:
            return {"tool":"kubectl_get","args":{"resourceType":"nodes"},"explanation":"Listing nodes"}
        if "namespaces" in q:
            return {"tool":"kubectl_get","args":{"resourceType":"namespaces"},"explanation":"Listing namespaces"}
        return {"tool":None,"args":{},"explanation":"No matching tool found"}
    if not GEMINI_AVAILABLE:
        return heuristic()
    return heuristic()  # Simplified: you can plug full Gemini logic here if needed

# ---------------- AUTH ----------------
def attempt_login(username: str, password: str) -> Dict[str, Any]:
    try:
        url = f"{API_URL}/login"
        resp = requests.post(url, json={"username": username, "password": password}, timeout=10)
        if resp.status_code != 200:
            return {"error": f"Login failed HTTP {resp.status_code}: {resp.text}"}
        data = resp.json()
        if not data.get("session_id"):
            data["session_id"] = data.get("session") or data.get("token")
        return data
    except Exception as e:
        return {"error": str(e)}

# ---------------- STREAMLIT APP ----------------
def main():
    st.set_page_config(page_title="MCP Chat Assistant", layout="wide")
    st.title("🤖 MaSaOps Bot (Gateway Version)")

    if "session" not in st.session_state:
        st.session_state.session = None
        st.session_state.username = None
        st.session_state.access = []
        st.session_state.messages = []
        st.session_state.last_known_cluster_name = None
        st.session_state.last_known_cluster_size = None
        st.session_state.available_servers = SERVERS

    # Sidebar login/settings
    with st.sidebar:
        st.header("👤 Profile")
        if st.session_state.session:
            st.write(f"**Username:** {st.session_state.username}")
            st.write(f"**Access:** {', '.join(st.session_state.access) if st.session_state.access else 'None'}")
            if st.button("Logout"):
                st.session_state.session = None
                st.session_state.username = None
                st.session_state.access = []
                st.rerun()
        else:
            st.write("Not logged in")
        st.title("⚙ Settings")
        st.text_input("Gemini API Key", value=GEMINI_API_KEY, disabled=True)

    if not st.session_state.session:
        st.subheader("Login")
        username = st.text_input("Username", key="login_user")
        password = st.text_input("Password", type="password", key="login_pass")
        if st.button("Login"):
            resp = attempt_login(username, password)
            if resp.get("session_id"):
                st.session_state.session = resp["session_id"]
                st.session_state.username = resp.get("username") or username
                st.session_state.access = resp.get("access") or []
                st.success(f"Logged in as {st.session_state.username}")
                st.rerun()
            else:
                st.error(f"Login failed: {resp.get('error')}")
        return

    # Chat input & handling
    st.subheader("Ask your MCP question")
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
    user_prompt = st.chat_input("Ask anything")
    if not user_prompt:
        return
    st.session_state.messages.append({"role":"user","content":user_prompt})
    st.chat_message("user").markdown(user_prompt)

    # Detect server
    selected_server = detect_server_from_query(user_prompt, SERVERS)
    if not selected_server:
        st.error("No server available")
        return
    chosen_server = selected_server["name"]
    st.chat_message("assistant").markdown(f"Using server: *{chosen_server}*")

    # Check access
    if chosen_server not in st.session_state.access and "all" not in st.session_state.access:
        msg = f"Access denied: you cannot access {chosen_server}"
        st.chat_message("assistant").markdown(msg)
        return

    # Determine tool
    decision = ask_gemini_for_tool_decision(user_prompt, chosen_server)
    chosen_tool = decision.get("tool")
    tool_args = decision.get("args")
    explanation = decision.get("explanation")
    st.chat_message("assistant").markdown(f"💡 {explanation}")

    if not chosen_tool:
        st.chat_message("assistant").markdown("Could not find a matching tool. Try a different query.")
        return

    # Call tool
    resp = call_tool(chosen_server, chosen_tool, tool_args, st.session_state.session)
    final_answer = json.dumps(resp, indent=2) if "error" not in resp else resp["error"]
    st.chat_message("assistant").markdown(final_answer)

if __name__ == "__main__":
    main()
