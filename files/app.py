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
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
GEMINI_AVAILABLE = False

if genai and GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        GEMINI_AVAILABLE = True
    except Exception:
        GEMINI_AVAILABLE = False

# ---------------- SERVERS ----------------
def load_servers_from_file() -> List[Dict[str, Any]]:
    """Load servers list from servers.json or fallback to sensible defaults."""
    try:
        with open("servers.json", "r") as f:
            data = json.load(f)
            servers = data.get("servers") or data.get("Servers") or []
            if isinstance(servers, list) and len(servers) > 0:
                return servers
    except Exception:
        pass
    # Fallback defaults
    return [
        {"name": "jenkins", "url": f"{API_URL}/mcp", "description": "Jenkins"},
        {"name": "kubernetes", "url": f"{API_URL}/mcp", "description": "Kubernetes"},
        {"name": "argocd", "url": f"{API_URL}/mcp", "description": "ArgoCD"},
    ]

SERVERS = load_servers_from_file()
SERVER_NAMES = [s.get("name") for s in SERVERS]

# ---------------- HELPERS ----------------
def sanitize_args(args: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Fix arguments before sending to MCP tools via gateway."""
    if not args:
        return {}

    fixed = dict(args)
    
    # Handle resource/resourceType naming
    if "resource" in fixed and "resourceType" not in fixed:
        fixed["resourceType"] = fixed.pop("resource")
    
    # Handle "all namespaces" request
    if fixed.get("namespace") == "all":
        fixed["allNamespaces"] = True
        fixed.pop("namespace", None)
    
    # Handle "all resources" request
    if fixed.get("resourceType") == "all":
        fixed["allResources"] = True
        fixed.pop("resourceType", None)
    
    # Handle common Kubernetes resource types
    resource_mappings = {
        "ns": "namespaces",
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
    
    # Auto-set allNamespaces for resources that support it
    if (fixed.get("resourceType") in ["pods", "services", "deployments", "secrets", "configmaps"] and 
        "namespace" not in fixed):
        fixed["allNamespaces"] = True
    
    return fixed

def _extract_json_from_text(text: str) -> Optional[dict]:
    """Extract JSON object from free text."""
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
    """Automatically detect which server to use based on query content."""
    query_lower = query.lower()
    
    # Check each server's tools to see which one matches the query
    for server in available_servers:
        try:
            tools = list_mcp_tools_for_server(server["name"])
            tool_names = [t.lower() for t in tools]
            
            # Check if any tool name is mentioned in the query
            for tool_name in tool_names:
                if tool_name in query_lower:
                    return server
            
            # Check for common keywords that match server types
            server_name = server["name"].lower()
            
            # Kubernetes queries
            if (("kubernetes" in query_lower or "k8s" in query_lower or 
                 "pod" in query_lower or "namespace" in query_lower or
                 "deployment" in query_lower or "service" in query_lower or
                 "secret" in query_lower or "configmap" in query_lower or
                 "node" in query_lower or "cluster" in query_lower or
                 "resource" in query_lower or "create" in query_lower or
                 "delete" in query_lower) and 
                ("kubernetes" in server_name or "k8s" in server_name)):
                return server
                
            # Jenkins queries
            if (("jenkins" in query_lower or "job" in query_lower or 
                 "build" in query_lower or "pipeline" in query_lower) and 
                "jenkins" in server_name):
                return server
                
            # ArgoCD queries
            if (("argocd" in query_lower or "application" in query_lower or 
                 "gitops" in query_lower or "sync" in query_lower) and 
                "argocd" in server_name):
                return server
                
        except Exception:
            continue
    
    # If no specific server detected, return the first available one
    return available_servers[0] if available_servers else None

# ---------------- GET ALL RESOURCES ----------------
def get_all_cluster_resources(server_name: str, session_id: str):
    """Get all resources in the cluster by querying multiple resource types via gateway."""
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
            
            # FIXED: Use correct JSON structure with session_id at top level
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
            response = requests.post(url, json=rpc_body, timeout=30)
            
            if response.status_code == 200:
                result = response.json()
                if isinstance(result, dict) and "result" in result:
                    all_resources[resource_type] = result["result"]
                else:
                    all_resources[resource_type] = result
            else:
                all_resources[resource_type] = f"Error: HTTP {response.status_code}"
                
            time.sleep(0.1)
            
        except Exception as e:
            all_resources[resource_type] = f"Exception: {str(e)}"
    
    return all_resources

# ---------------- TOOL CALL ----------------
def call_tool(server_name: str, name: str, arguments: dict, session_id: str):
    """Execute MCP tool by name with arguments via gateway."""
    if not name or not isinstance(arguments, dict):
        return {"error": "Invalid tool name or arguments"}
    
    # FIXED: Create full JSON-RPC body with session_id at top level
    rpc_body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": name,
            "arguments": arguments
        },
        "session_id": session_id  # CRITICAL: session_id at top level for your auth gateway
    }
    
    url = f"{API_URL}/mcp?target={server_name}"
    try:
        response = requests.post(url, json=rpc_body, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        return {"error": f"Gateway request failed: {str(e)}"}

# ---------------- LIST TOOLS ----------------
def list_mcp_tools_for_server(server_name: str) -> List[str]:
    """List tools by calling gateway tools/list on that server name."""
    try:
        # FIXED: Use correct format for tools/list
        rpc_body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {},
            "session_id": "dummy"  # tools/list doesn't require auth, but gateway expects session_id
        }
        
        url = f"{API_URL}/mcp?target={server_name}"
        response = requests.post(url, json=rpc_body, timeout=10)
        response.raise_for_status()
        
        result = response.json()
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

# ---------------- GEMINI: pick tool + args ----------------
def ask_gemini_for_tool_decision(query: str, server_name: str, retries: int = 2) -> Dict[str, Any]:
    """Use Gemini to map user query to tool name and arguments via gateway."""
    available_tools = list_mcp_tools_for_server(server_name)

    # Enhanced fallback logic
    def heuristic():
        q = query.lower()
        candidate = {"tool": None, "args": {}, "explanation": "Used local heuristic fallback."}
        
        # Handle namespace creation
        if any(word in q for word in ["create namespace", "create ns", "make namespace"]):
            namespace_match = re.search(r'(?:namespace|ns)[\s]+([\w-]+)', q)
            if namespace_match:
                namespace_name = namespace_match.group(1)
                return {
                    "tool": "kubectl_create",
                    "args": {"resourceType": "namespaces", "name": namespace_name},
                    "explanation": f"Creating namespace '{namespace_name}'"
                }
        
        # Handle namespace deletion
        if any(word in q for word in ["delete namespace", "delete ns", "remove namespace"]):
            namespace_match = re.search(r'(?:namespace|ns)[\s]+([\w-]+)', q)
            if namespace_match:
                namespace_name = namespace_match.group(1)
                return {
                    "tool": "kubectl_delete",
                    "args": {"resourceType": "namespaces", "name": namespace_name},
                    "explanation": f"Deleting namespace '{namespace_name}'"
                }
        
        # Handle "all resources" requests
        if any(phrase in q for phrase in ["all pods", "all resources", "everything", "all namespaces"]):
            return {
                "tool": "kubectl_get",
                "args": {"resourceType": "all", "allNamespaces": True},
                "explanation": "User wants to see all resources across all namespaces"
            }
        elif "pods" in q or "pod" in q:
            return {
                "tool": "kubectl_get",
                "args": {"resourceType": "pods", "allNamespaces": True},
                "explanation": "User wants to see all pods across all namespaces"
            }
        elif "services" in q or "svc" in q:
            return {
                "tool": "kubectl_get",
                "args": {"resourceType": "services", "allNamespaces": True},
                "explanation": "User wants to see all services across all namespaces"
            }
        elif "nodes" in q:
            return {
                "tool": "kubectl_get",
                "args": {"resourceType": "nodes"},
                "explanation": "User wants to see all nodes"
            }
        else:
            return {"tool": None, "args": {}, "explanation": "No specific tool matched"}

    if not GEMINI_AVAILABLE:
        return heuristic()

    # Build instruction for Gemini
    instruction = f"""
You are an assistant that maps a user's query to a tool name and args.
User query: "{query}"
Available tools for server '{server_name}': {json.dumps(available_tools, indent=2)}

IMPORTANT RULES FOR KUBERNETES:
- When user asks for "all pods", "show all pods", or similar, ALWAYS set allNamespaces=true
- When user wants to see resources across all namespaces, use allNamespaces=true
- For pods, services, deployments, secrets, configmaps - default to all namespaces unless specified
- For nodes and namespaces, don't use namespace parameters
- For creating namespaces: use kubectl_create with resourceType: "namespaces" and name: "<namespace_name>"
- For deleting namespaces: use kubectl_delete with resourceType: "namespaces" and name: "<namespace_name>"

Rules:
- Only choose tools from the available list above.
- Respond in strict JSON: {{"tool": "<tool_name_or_null>", "args": {{...}} | null, "explanation": "short"}}
If unsure, set tool to null.
"""
    for attempt in range(retries):
        try:
            model = genai.GenerativeModel(GEMINI_MODEL)
            resp = model.generate_content(instruction)
            text = getattr(resp, "text", str(resp)).strip()
            parsed = None
            
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = _extract_json_from_text(text)
                
            if not isinstance(parsed, dict):
                return heuristic()
                
            # sanitize
            parsed["args"] = sanitize_args(parsed.get("args") or {})
            
            # validate tool exists
            tool = parsed.get("tool")
            if tool and tool not in available_tools:
                parsed["explanation"] = f"Tool '{tool}' not available on server '{server_name}'."
                parsed["tool"] = None
                
            return parsed
        except Exception:
            time.sleep(1)
            continue
            
    return heuristic()

# ---------------- GEMINI: friendly answer ----------------
def ask_gemini_answer(user_input: str, raw_response: dict) -> str:
    """Ask Gemini to turn raw gateway response into human-friendly answer."""
    if not GEMINI_AVAILABLE:
        return generate_fallback_answer(user_input, raw_response)
    
    try:
        context_notes = ""
        if "last_known_cluster_name" in st.session_state:
            context_notes += f"\nPreviously known cluster: {st.session_state['last_known_cluster_name']}"
        if "last_known_cluster_size" in st.session_state:
            context_notes += f"\nPreviously known cluster size: {st.session_state['last_known_cluster_size']}"

        prompt = (
            f"User asked: {user_input}\n"
            f"Context: {context_notes}\n"
            f"Raw system response:\n{json.dumps(raw_response, indent=2)}\n"
            "INSTRUCTIONS:\n"
            "- Respond in clear, natural, conversational English.\n"
            "- If it's a list, format with bullet points.\n"
            "- If it's status, explain health and issues clearly.\n"
            "- If error occurred, DO NOT show raw error. Politely explain what went wrong and suggest what user can do.\n"
            "- If cluster name or size was inferred, mention that explicitly.\n"
            "- If cluster size = 1, say: 'This appears to be a minimal/single-node cluster.'\n"
            "- NEVER show JSON, code, or internal errors to user unless asked.\n"
            "- Be helpful, friendly, and precise.\n"
        )
        model = genai.GenerativeModel(GEMINI_MODEL)
        resp = model.generate_content(prompt)
        answer = getattr(resp, "text", str(resp)).strip()
        
        # try to extract cluster info
        extract_and_store_cluster_info(user_input, answer)
        return answer
    except Exception:
        return generate_fallback_answer(user_input, raw_response)

def generate_fallback_answer(user_input: str, raw_response: dict) -> str:
    """Generate human-friendly answer without Gemini."""
    if "error" in raw_response:
        error_msg = raw_response["error"]
        
        # Handle namespace creation errors
        if "create" in user_input.lower() and "namespace" in user_input.lower():
            if "already exists" in error_msg.lower():
                return "This namespace already exists in the cluster."
            elif "forbidden" in error_msg.lower() or "permission" in error_msg.lower():
                return "I don't have permission to create namespaces. Please check your Kubernetes RBAC permissions."
            else:
                return f"Sorry, I couldn't create the namespace: {error_msg}"
        
        if "cluster" in user_input.lower():
            return "I couldn't retrieve the cluster information right now. Please check if the gateway server is running and accessible."
        return f"Sorry, I encountered an issue: {error_msg}"

    result = raw_response.get("result", {})

    # Handle different response formats
    if isinstance(result, dict):
        # Kubernetes-style responses with items
        if "items" in result:
            items = result["items"]
            count = len(items)
            
            if "node" in user_input.lower() or "cluster size" in user_input.lower():
                if count == 1:
                    node_name = items[0].get("metadata", {}).get("name", "unknown")
                    return f"This is a single-node cluster. The node is named: {node_name}"
                else:
                    return f"The cluster has {count} nodes."
            
            if "namespace" in user_input.lower():
                namespaces = [item.get("metadata", {}).get("name", "unnamed") for item in items]
                if namespaces:
                    return f"Found {count} namespaces:\n" + "\n".join([f"• {ns}" for ns in namespaces])
                else:
                    return "No namespaces found."
            
            if "pod" in user_input.lower():
                pods_info = []
                for item in items:
                    name = item.get('metadata', {}).get('name', 'unnamed')
                    namespace = item.get('metadata', {}).get('namespace', 'default')
                    status = item.get('status', {}).get('phase', 'Unknown')
                    pods_info.append(f"{name} (Namespace: {namespace}, Status: {status})")
                
                if pods_info:
                    return f"Found {count} pods across all namespaces:\n" + "\n".join([f"• {pod}" for pod in pods_info])
                else:
                    return "No pods found in any namespace."

        # Handle all-resources response
        if any(key in result for key in ["pods", "services", "deployments", "configmaps", "secrets", "namespaces", "nodes"]):
            summary = "**Cluster Resources Summary:**\n\n"
            total_count = 0
            
            for resource_type, resources in result.items():
                if isinstance(resources, list):
                    count = len(resources)
                    total_count += count
                    summary += f"• {resource_type.capitalize()}: {count}\n"
            
            return f"{summary}\nTotal resources found: {total_count}"
        
        # Jenkins-style responses
        if "jobs" in result:
            jobs = result["jobs"]
            if jobs:
                return f"Found {len(jobs)} Jenkins jobs:\n" + "\n".join([f"• {job.get('name', 'unnamed')}" for job in jobs])
            else:
                return "No Jenkins jobs found."
        
        # ArgoCD-style responses
        if "applications" in result:
            apps = result["applications"]
            if apps:
                return f"Found {len(apps)} ArgoCD applications:\n" + "\n".join([f"• {app.get('name', 'unnamed')}" for app in apps])
            else:
                return "No ArgoCD applications found."

    # Generic fallback for successful operations
    if result and not result.get("error"):
        if "create" in user_input.lower():
            return "✅ Operation completed successfully!"
        elif "delete" in user_input.lower():
            return "✅ Operation completed successfully!"
        else:
            return "✅ Operation completed successfully. Found data across all namespaces."
    
    return "Operation completed, but no specific data was returned."

def extract_and_store_cluster_info(user_input: str, answer: str):
    """Extract cluster name/size from answer and store in session."""
    try:
        # Extract cluster name
        if "cluster name" in user_input.lower():
            patterns = [
                r"cluster[^\w]*([\w-]+)",
                r"name[^\w][:\-]?[^\w]([\w-]+)",
            ]
            for pattern in patterns:
                match = re.search(pattern, answer, re.IGNORECASE)
                if match:
                    cluster_name = match.group(1).strip()
                    st.session_state["last_known_cluster_name"] = cluster_name
                    break

        # Extract cluster size
        if "cluster size" in user_input.lower() or "how many nodes" in user_input.lower():
            numbers = re.findall(r'\b\d+\b', answer)
            if numbers:
                st.session_state["last_known_cluster_size"] = int(numbers[0])
    except Exception:
        pass

# ---------------- AUTH (login) ----------------
def attempt_login(username: str, password: str) -> Dict[str, Any]:
    try:
        response = requests.post(f"{API_URL}/login", json={"username": username, "password": password}, timeout=10)
        if response.status_code == 200:
            return response.json()
        try:
            return {"error": response.json()}
        except Exception:
            return {"error": response.text}
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
