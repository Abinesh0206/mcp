import os
import json
import time
import requests
import streamlit as st
from dotenv import load_dotenv
import google.generativeai as genai
from typing import Optional, Dict, Any
import re

# ---------------- CONFIG ----------------
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyC7iRO4NnyQz144aEc6RiVUNzjL9C051V8")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

# Configure Gemini if available
GEMINI_AVAILABLE = False
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        GEMINI_AVAILABLE = True
    except Exception:
        GEMINI_AVAILABLE = False

# Load servers list from servers.json
def load_servers() -> list:
    try:
        with open("servers.json") as f:
            data = json.load(f)
        return data.get("servers", [])
    except Exception:
        return []

SERVERS = load_servers()

# Initialize session state
if "messages" not in st.session_state:
    st.session_state.messages = []
    st.session_state.last_known_cluster_name = None
    st.session_state.last_known_cluster_size = None
    st.session_state.available_servers = SERVERS

# ---------------- HELPERS ----------------
def direct_mcp_call(server_url: str, method: str, params: Optional[Dict[str, Any]] = None, timeout: int = 30) -> Dict[str, Any]:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or {}
    }
    
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream, */*"
    }
    
    try:
        response = requests.post(server_url, json=payload, headers=headers, timeout=timeout)
        response.raise_for_status()
        
        text = response.text.strip()
        if text.startswith("data:") or "data:" in text:
            lines = text.split('\n')
            for line in lines:
                if line.startswith('data:'):
                    data_content = line[5:].strip()
                    try:
                        return json.loads(data_content)
                    except json.JSONDecodeError:
                        return {"result": data_content}
        
        try:
            return response.json()
        except json.JSONDecodeError:
            return {"result": text}
            
    except requests.exceptions.RequestException as e:
        return {"error": f"MCP server request failed: {str(e)}"}
    except Exception as e:
        return {"error": f"Unexpected error: {str(e)}"}

def list_mcp_tools(server_url: str):
    resp = direct_mcp_call(server_url, "tools/list")
    if not isinstance(resp, dict):
        return []
    result = resp.get("result", {})
    if isinstance(result, dict):
        return result.get("tools", [])
    if isinstance(result, list):
        return result
    if "tools" in resp:
        return resp["tools"]
    return []

def call_tool(server_url: str, name: str, arguments: dict):
    if not name or not isinstance(arguments, dict):
        return {"error": "Invalid tool name or arguments"}
    return direct_mcp_call(server_url, "tools/call", {
        "name": name,
        "arguments": arguments
    })

def sanitize_args(args: dict):
    """Fix arguments before sending to MCP tools."""
    if not args:
        return {}

    fixed = args.copy()
    
    if "resource" in fixed and "resourceType" not in fixed:
        fixed["resourceType"] = fixed.pop("resource")
    
    if (fixed.get("resourceType") in ["pods", "services", "deployments", "secrets", "configmaps"] and 
        "namespace" not in fixed):
        fixed["allNamespaces"] = True
    
    if fixed.get("namespace") == "all":
        fixed["allNamespaces"] = True
        fixed.pop("namespace", None)
    
    if fixed.get("resourceType") == "all":
        fixed["allResources"] = True
        fixed.pop("resourceType", None)
    
    resource_mappings = {
        "ns": "namespaces",
        "namespace": "namespaces",
        "pod": "pods",
        "node": "nodes",
        "deploy": "deployments",
        "svc": "services",
        "cm": "configmaps",
        "secret": "secrets",
        "all": "all"
    }
    
    if fixed.get("resourceType") in resource_mappings:
        fixed["resourceType"] = resource_mappings[fixed["resourceType"]]
    
    # ✅ Serialize namespace manifest to JSON string
    if fixed.get("resourceType") == "namespaces" and "name" in fixed:
        if "manifest" not in fixed and "filename" not in fixed:
            namespace_name = fixed.get("name")
            fixed["manifest"] = json.dumps({
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": {
                    "name": namespace_name
                }
            })
    
    return fixed

def _extract_json_from_text(text: str) -> Optional[dict]:
    try:
        start = text.find('{')
        end = text.rfind('}') + 1
        if start != -1 and end != -1 and end > start:
            json_str = text[start:end]
            return json.loads(json_str)
    except Exception:
        pass
    return None

def detect_server_from_query(query: str, available_servers: list) -> Optional[Dict[str, Any]]:
    query_lower = query.lower()
    for server in available_servers:
        try:
            tools = list_mcp_tools(server["url"])
            tool_names = [t.get("name", "").lower() for t in tools if t.get("name")]
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
        except Exception:
            continue
    return available_servers[0] if available_servers else None

def get_all_cluster_resources(server_url: str):
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
            response = call_tool(server_url, "kubectl_get", params)
            if response and not response.get("error"):
                result = response.get("result", {})
                if isinstance(result, dict) and "items" in result:
                    all_resources[resource_type] = result["items"]
                else:
                    all_resources[resource_type] = result
            else:
                all_resources[resource_type] = f"Error: {response.get('error', 'Unknown error')}"
            time.sleep(0.1)
        except Exception as e:
            all_resources[resource_type] = f"Exception: {str(e)}"
    
    return all_resources

# ---------------- GEMINI FUNCTIONS ----------------
def ask_gemini_for_tool_decision(query: str, server_url: str):
    tools = list_mcp_tools(server_url)
    tool_names = [t["name"] for t in tools if "name" in t]
    context_notes = ""
    if st.session_state.last_known_cluster_name:
        context_notes += f"\nUser previously interacted with cluster: {st.session_state.last_known_cluster_name}"
    if st.session_state.last_known_cluster_size:
        context_notes += f"\nLast known cluster size: {st.session_state.last_known_cluster_size} nodes"

    if not GEMINI_AVAILABLE:
        query_lower = query.lower()
        if any(word in query_lower for word in ["create namespace", "create ns", "make namespace"]):
            namespace_match = re.search(r'(?:namespace|ns)[\s]+([\w-]+)', query_lower)
            if namespace_match:
                namespace_name = namespace_match.group(1)
                return {
                    "tool": "kubectl_create",
                    "args": {"resourceType": "namespaces", "name": namespace_name},
                    "explanation": f"Creating namespace '{namespace_name}'"
                }
        return {"tool": None, "args": None, "explanation": "Gemini not configured; fallback to chat reply."}
    
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content("Map user query to MCP tool JSON...")
        text = response.text.strip()
        parsed = None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = _extract_json_from_text(text)
        if not parsed:
            parsed = {"tool": None, "args": None, "explanation": f"Gemini invalid response: {text}"}
        parsed["args"] = sanitize_args(parsed.get("args") or {})
        return parsed
    except Exception as e:
        return {"tool": None, "args": None, "explanation": f"Gemini error: {str(e)}"}

def ask_gemini_answer(user_input: str, raw_response: dict) -> str:
    return "✅ Operation completed successfully!"  # simplified for brevity

# ---------------- STREAMLIT APP ----------------
def main():
    st.set_page_config(page_title="MCP Chat Assistant", page_icon="⚡", layout="wide")
    st.title("🤖 MaSaOps Bot")

    with st.sidebar:
        st.header("⚙️ Settings")
        if st.button("Discover Available Servers"):
            with st.spinner("Discovering MCP servers..."):
                st.success(f"Found {len(SERVERS)} servers")
                for server in SERVERS:
                    st.write(f"• {server['name']}: {server['url']}")
        st.text_input("Gemini API Key", value=GEMINI_API_KEY, disabled=True, type="password")
        if st.button("Clear Chat History"):
            st.session_state.messages = []
            st.rerun()

    st.subheader("What's on your mind today? 🤔")
    for msg in st.session_state.messages:
        role = msg.get("role", "assistant")
        with st.chat_message(role):
            st.markdown(msg.get("content", ""))
    
    user_prompt = st.chat_input("Ask anything about your infrastructure...")
    if not user_prompt:
        return
    
    st.session_state.messages.append({"role": "user", "content": user_prompt})
    with st.chat_message("user"):
        st.markdown(user_prompt)
    
    with st.spinner("🔍 Finding the right server for your query..."):
        selected_server = detect_server_from_query(user_prompt, SERVERS)
    
    if not selected_server:
        error_msg = "No MCP servers available. Please check your servers.json file."
        st.session_state.messages.append({"role": "assistant", "content": error_msg})
        with st.chat_message("assistant"):
            st.error(error_msg)
        return
    
    server_info = f"🤖 Using server: **{selected_server['name']}**"
    st.session_state.messages.append({"role": "assistant", "content": server_info})
    with st.chat_message("assistant"):
        st.markdown(server_info)
    
    with st.spinner("🧠 Analyzing user query with Gemini (or fallback)..."):
        gemini_result = ask_gemini_for_tool_decision(user_prompt, selected_server["url"])
    
    tool_to_call = gemini_result.get("tool")
    tool_args = gemini_result.get("args")
    explanation = gemini_result.get("explanation")
    
    st.session_state.messages.append({"role": "assistant", "content": f"*{explanation}*"})
    with st.chat_message("assistant"):
        st.markdown(f"*{explanation}*")
    
    if tool_to_call and tool_args:
        with st.spinner(f"⚡ Executing tool: {tool_to_call}..."):
            result = call_tool(selected_server["url"], tool_to_call, tool_args)
        readable_result = json.dumps(result, indent=2)
        st.session_state.messages.append({"role": "assistant", "content": f"```\n{readable_result}\n```"})
        with st.chat_message("assistant"):
            st.code(readable_result)
    else:
        fallback_msg = "I could not map your request to any MCP tool. Try rephrasing your query."
        st.session_state.messages.append({"role": "assistant", "content": fallback_msg})
        with st.chat_message("assistant"):
            st.warning(fallback_msg)

if __name__ == "__main__":
    main()
