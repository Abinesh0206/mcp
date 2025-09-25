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

# ---------------- HTTP helpers (AUTH placement) ----------------
def build_auth_headers_and_cookies(session_id: Optional[str]) -> Dict[str, Any]:
    headers = {}
    cookies = {}
    if session_id:
        headers["Authorization"] = f"Bearer {session_id}"
        # Some gateways expect custom header names; include one more popular header just in case:
        headers["X-Session-Id"] = session_id
        cookies["session_id"] = session_id
    headers["Content-Type"] = "application/json"
    return {"headers": headers, "cookies": cookies}

# ---------------- CALL TOOL ----------------
def call_tool(server_name: str, name: str, arguments: dict, session_id: Optional[str]):
    """Execute MCP tool by name with arguments via gateway. Sends session as header + cookie + json."""
    if not name or not isinstance(arguments, dict):
        return {"error": "Invalid tool name or arguments"}

    rpc_body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": name,
            "arguments": arguments
        },
        # keep session_id in body for compatibility
        "session_id": session_id
    }

    url = f"{API_URL}/mcp?target={server_name}"
    auth = build_auth_headers_and_cookies(session_id)
    try:
        resp = requests.post(url, json=rpc_body, headers=auth["headers"], cookies=auth["cookies"], timeout=20)
        # Helpful debugging when something goes wrong
        if resp.status_code == 401:
            return {"error": f"Gateway returned 401 Unauthorized. Check login/session token. Response: {resp.text}"}
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        return {"error": f"Gateway request failed: {str(e)}"}

# ---------------- LIST TOOLS ----------------
def list_mcp_tools_for_server(server_name: str, session_id: Optional[str] = None) -> List[str]:
    """List tools by calling gateway tools/list on that server name."""
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
            # return empty and let caller know
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
    resource_types = [
        "pods", "services", "deployments", "configmaps",
        "secrets", "namespaces", "nodes"
    ]
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
                "params": {
                    "name": "kubectl_get",
                    "arguments": params
                },
                "session_id": session_id
            }
            url = f"{API_URL}/mcp?target={server_name}"
            auth = build_auth_headers_and_cookies(session_id)
            response = requests.post(url, json=rpc_body, headers=auth["headers"], cookies=auth["cookies"], timeout=30)
            if response.status_code == 401:
                all_resources[resource_type] = "Error: 401 Unauthorized (check session/login)"
                continue
            if response.status_code != 200:
                all_resources[resource_type] = f"Error: HTTP {response.status_code} - {response.text}"
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

# ---------------- AUTH (login) ----------------
def attempt_login(username: str, password: str) -> Dict[str, Any]:
    try:
        url = f"{API_URL}/login"
        resp = requests.post(url, json={"username": username, "password": password}, timeout=10)
        if resp.status_code != 200:
            return {"error": f"Login failed HTTP {resp.status_code}: {resp.text}"}
        data = resp.json()
        # Expecting session_id in response
        if not data.get("session_id"):
            # Try common alternatives
            if data.get("session"):
                data["session_id"] = data.get("session")
            elif data.get("token"):
                data["session_id"] = data.get("token")
        return data
    except Exception as e:
        return {"error": str(e)}

# ---------------- STREAMLIT APP ----------------
def main():
    st.set_page_config(page_title="MCP Chat Assistant", page_icon="⚡", layout="wide")
    st.title("🤖 MaSaOps Bot (Gateway Version)")

    # session defaults
    if "session" not in st.session_state:
        st.session_state.session = None
        st.session_state.username = None
        st.session_state.access = []
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "last_known_cluster_name" not in st.session_state:
        st.session_state["last_known_cluster_name"] = None
    if "last_known_cluster_size" not in st.session_state:
        st.session_state["last_known_cluster_size"] = None
    if "available_servers" not in st.session_state:
        st.session_state["available_servers"] = SERVERS

    # Sidebar: profile, settings
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
        st.markdown("**Providers & Keys**")
        st.text_input("Gemini API Key (env)", value=(GEMINI_API_KEY or ""), disabled=True)
        models = [GEMINI_MODEL, "gemini-1.0", "gemini-1.5-pro", "gemini-2.0-flash"]
        sel = st.selectbox("Gemini model", options=models, index=0)
        st.session_state["gemini_model"] = sel

        if st.button("Clear chat history"):
            st.session_state["messages"] = []
            st.rerun()

    # If not logged in -> show login form
    if not st.session_state.session:
        st.subheader("Login")
        username = st.text_input("Username", key="login_user")
        password = st.text_input("Password", type="password", key="login_pass")
        if st.button("Login", key="login_btn"):
            resp = attempt_login(username, password)
            if resp and resp.get("session_id"):
                st.session_state.session = resp.get("session_id")
                st.session_state.username = resp.get("username") or username
                st.session_state.access = resp.get("access", []) or []
                st.success(f"Logged in as {st.session_state.username}.")
                # seed a welcome message
                access_str = ", ".join(st.session_state.access)
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": f"Welcome {st.session_state.username}! Ask me about {access_str}."
                })
                st.rerun()
            else:
                st.error(f"Login failed: {resp.get('error') if resp else 'unknown error'}")
        st.info("This app requires a working API_URL login endpoint that returns JSON with session_id and access list.")
        return  # stop rendering rest until logged in

    # Chat UI
    st.subheader("What's on your mind today? 🤔")

    # Render chat history
    for msg in st.session_state.messages:
        with st.chat_message(msg.get("role", "assistant")):
            st.markdown(msg.get("content", ""))

    user_prompt = st.chat_input("Ask anything about MCP (e.g., list pods, list applications, cluster size...)")
    if not user_prompt:
        return

    # append user message
    st.session_state.messages.append({"role": "user", "content": user_prompt})
    st.chat_message("user").markdown(user_prompt)

    # Auto-detect which server to use based on query
    with st.spinner("🔍 Finding the right server for your query..."):
        selected_server = detect_server_from_query(user_prompt, SERVERS)

    if not selected_server:
        error_msg = "No MCP servers available. Please check your servers.json file."
        st.session_state.messages.append({"role": "assistant", "content": error_msg})
        st.chat_message("assistant").error(error_msg)
        return

    chosen_server = selected_server.get("name")
    # Show which server we're using
    server_info = f"🤖 Using server: *{chosen_server}*"
    st.session_state.messages.append({"role": "assistant", "content": server_info})
    st.chat_message("assistant").markdown(server_info)

    # RBAC: ensure user has access to chosen_server
    if chosen_server not in st.session_state.access:
        # If user has wildcard or full access, allow; otherwise deny
        if "all" not in st.session_state.access and "admin" not in st.session_state.access:
            deny_msg = f"🚫 Access denied: your access list ({', '.join(st.session_state.access) or 'none'}) doesn't include {chosen_server}."
            st.session_state.messages.append({"role":"assistant","content":deny_msg})
            st.chat_message("assistant").markdown(deny_msg)
            return

    # Use Gemini (or heuristic) to pick tool + args
    with st.spinner("🤔 Analyzing your request..."):
        decision = ask_gemini_for_tool_decision(user_prompt, chosen_server)

    explanation = decision.get("explanation") or "Deciding how to help..."
    st.session_state.messages.append({"role": "assistant", "content": f"💡 {explanation}"})
    st.chat_message("assistant").markdown(f"💡 {explanation}")

    chosen_tool = decision.get("tool")
    tool_args = decision.get("args") or {}

    # If no tool chosen, offer suggestions
    if not chosen_tool:
        help_msg = (
            "I couldn't find a direct tool to answer your question. Try:\n"
            "- 'List pods in all namespaces'\n"
            "- 'List applications'\n"
            "- 'How many nodes in the cluster?'\n"
            "- 'Create namespace test'\n"
            "- 'Delete namespace test'\n"
            "**For Kubernetes:**\n"
            "- \"List all namespaces\"\n"
            "- \"Show all pods across all namespaces\"\n"
            "- \"Get cluster nodes\"\n"
            "- \"Show all services\"\n"
            "- \"List all secrets\"\n"
            "- \"Show all resources in cluster\"\n"
            "**For Jenkins:**\n"
            "- \"List all jobs\"\n"
            "- \"Show build status\"\n"
            "**For ArgoCD:**\n"
            "- \"List applications\"\n"
            "- \"Show application status\"\n"
            "Or try being more specific about what you'd like to see!"
        )
        st.session_state.messages.append({"role":"assistant","content":help_msg})
        st.chat_message("assistant").markdown(help_msg)
        return

    # Show call summary
    st.chat_message("assistant").markdown(f"🔧 Calling `{chosen_tool}` on `{chosen_server}` with args:\n```json\n{json.dumps(tool_args, indent=2)}\n```")

    # Special handling for "all resources" request
    if (user_prompt.lower().strip() in ["show me all resources in cluster", "get all resources", "all resources"] or
        ("all" in user_prompt.lower() and "resource" in user_prompt.lower())):
        with st.spinner("🔄 Gathering all cluster resources (this may take a moment)..."):
            all_resources = get_all_cluster_resources(chosen_server, st.session_state.session)
            resp = {"result": all_resources}
    else:
        # Perform gateway call with session id
        resp = call_tool(chosen_server, chosen_tool, tool_args, st.session_state.session)

    # Smart fallbacks: if expecting cluster name and resp empty/error -> try nodes
    if ("cluster name" in user_prompt.lower()) and (not resp or resp.get("error")):
        st.chat_message("assistant").markdown("📌 Attempting to infer cluster name from nodes...")
        
        # FIXED: Use correct format with session_id at top level
        rpc_body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "kubectl_get",
                "arguments": {"resourceType": "nodes", "format": "json"}
            },
            "session_id": st.session_state.session
        }
        
        url = f"{API_URL}/mcp?target={chosen_server}"
        try:
            node_resp = requests.post(url, json=rpc_body, timeout=20)
            if node_resp.status_code == 200:
                result = node_resp.json()
                items = result.get("result", {}).get("items") if isinstance(result.get("result"), dict) else None
                if items and len(items) > 0:
                    first_node = items[0].get("metadata", {}).get("name", "unknown")
                    cluster_hint = first_node.split(".")[0] if "." in first_node else first_node
                    st.session_state["last_known_cluster_name"] = cluster_hint
                    resp = {"result": {"inferred_cluster_name": cluster_hint}}
                    st.chat_message("assistant").markdown(f"✅ I inferred the cluster name: *{cluster_hint}*")
        except Exception as e:
            st.chat_message("assistant").markdown(f"Failed to get nodes: {str(e)}")

    # Smart cluster size handling
    if "cluster size" in user_prompt.lower() and chosen_tool == "kubectl_get" and tool_args.get("resourceType") == "nodes":
        if not resp.get("error") and isinstance(resp.get("result"), dict):
            items = resp["result"].get("items", [])
            node_count = len(items)
            st.session_state["last_known_cluster_size"] = node_count
            if node_count == 1:
                node_name = items[0].get("metadata", {}).get("name", "unknown")
                resp["result"]["_note"] = f"Single-node cluster. Node: {node_name}"

    # Turn raw response into friendly answer (Gemini or fallback)
    with st.spinner("📝 Formatting response..."):
        final_answer = ask_gemini_answer(user_prompt, resp)

    st.session_state.messages.append({"role":"assistant","content":final_answer})
    st.chat_message("assistant").markdown(final_answer)

# run app
if __name__ == "__main__":
    main()
