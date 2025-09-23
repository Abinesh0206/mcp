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
    st.session_state.last_known_namespace = None  # ✅ NEW: Track last namespace
    st.session_state.available_servers = SERVERS

# ---------------- HELPERS ----------------
def direct_mcp_call(server_url: str, method: str, params: Optional[Dict[str, Any]] = None, timeout: int = 30) -> Dict[str, Any]:
    """Direct call to MCP server with JSON-RPC payload"""
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
        
        # Handle SSE-style responses
        if text.startswith("data:") or "data:" in text:
            lines = text.split('\n')
            for line in lines:
                if line.startswith('data:'):
                    data_content = line[5:].strip()
                    try:
                        return json.loads(data_content)
                    except json.JSONDecodeError:
                        return {"result": data_content}
        
        # Handle regular JSON
        try:
            return response.json()
        except json.JSONDecodeError:
            return {"result": text}
            
    except requests.exceptions.RequestException as e:
        return {"error": f"MCP server request failed: {str(e)}"}
    except Exception as e:
        return {"error": f"Unexpected error: {str(e)}"}

def list_mcp_tools(server_url: str):
    """Fetch available MCP tools for a specific server."""
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
    """Execute MCP tool by name with arguments."""
    if not name or not isinstance(arguments, dict):
        return {"error": "Invalid tool name or arguments"}
    return direct_mcp_call(server_url, "tools/call", {
        "name": name,
        "arguments": arguments
    })

def sanitize_args(args: dict, user_query: str = ""):
    """Fix arguments before sending to MCP tools — now with namespace extraction from query."""
    if not args:
        args = {}

    fixed = args.copy()
    
    # Extract namespace from user query if not provided
    if "namespace" not in fixed and user_query:
        # Patterns: "in ns X", "in namespace X", "-n X", "X namespace"
        patterns = [
            r"in\s+ns\s+(\S+)",
            r"in\s+namespace\s+(\S+)",
            r"-n\s+(\S+)",
            r"(\S+)\s+namespace",
            r"namespace\s+(\S+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, user_query, re.IGNORECASE)
            if match:
                fixed["namespace"] = match.group(1)
                break

    # Handle resource/resourceType naming
    if "resource" in fixed and "resourceType" not in fixed:
        fixed["resourceType"] = fixed.pop("resource")
    
    # Handle events
    if fixed.get("resourceType") == "events":
        if "namespace" not in fixed:
            fixed["allNamespaces"] = True
        elif fixed.get("namespace") == "all":
            fixed["allNamespaces"] = True
            fixed.pop("namespace", None)
    
    # Set default namespace for pods if not specified
    elif fixed.get("resourceType") == "pods" and "namespace" not in fixed:
        fixed["namespace"] = st.session_state.get("last_known_namespace", "default")
    
    # Handle "all namespaces"
    elif fixed.get("namespace") == "all":
        fixed["allNamespaces"] = True
        fixed.pop("namespace", None)
    
    # Handle "all resources"
    if fixed.get("resourceType") == "all":
        fixed["allResources"] = True
        fixed.pop("resourceType", None)
    
    # Resource type mappings
    resource_mappings = {
        "ns": "namespaces",
        "pod": "pods",
        "node": "nodes",
        "deploy": "deployments",
        "svc": "services",
        "cm": "configmaps",
        "secret": "secrets",
        "event": "events",
        "ev": "events",
        "all": "all"
    }
    
    if fixed.get("resourceType") in resource_mappings:
        fixed["resourceType"] = resource_mappings[fixed["resourceType"]]
    
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
                 "resource" in query_lower or "error" in query_lower or
                 "event" in query_lower or "pending" in query_lower or
                 "describe" in query_lower) and 
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

def get_all_cluster_resources(server_url: str):
    resource_types = [
        "pods", "services", "deployments", "configmaps", 
        "secrets", "namespaces", "nodes", "events"
    ]
    
    all_resources = {}
    
    for resource_type in resource_types:
        try:
            params = {"resourceType": resource_type}
            if resource_type == "events":
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

def get_cluster_events_with_errors(server_url: str):
    try:
        response = call_tool(server_url, "kubectl_get", {
            "resourceType": "events",
            "allNamespaces": True
        })
        
        if response and not response.get("error"):
            result = response.get("result", {})
            events = result.get("items", []) if isinstance(result, dict) else result
            
            error_events = []
            if isinstance(events, list):
                for event in events:
                    if isinstance(event, dict):
                        event_type = event.get('type', '').lower()
                        reason = event.get('reason', '').lower()
                        message = event.get('message', '').lower()
                        
                        error_keywords = ['error', 'failed', 'backoff', 'crash', 'unhealthy', 'invalid']
                        if (event_type == 'error' or 
                            any(keyword in reason for keyword in error_keywords) or
                            any(keyword in message for keyword in error_keywords)):
                            error_events.append(event)
            
            return error_events
        else:
            return [{"error": response.get("error", "Failed to retrieve events")}]
            
    except Exception as e:
        return [{"error": f"Exception while getting events: {str(e)}"}]

def get_pending_pods_with_reason(server_url: str):
    """Get all pending pods and use kubectl_describe to find scheduling reason."""
    try:
        pods_response = call_tool(server_url, "kubectl_get", {
            "resourceType": "pods",
            "allNamespaces": True
        })
        
        if pods_response.get("error"):
            return [{"error": pods_response.get("error")}]

        pods = pods_response.get("result", {}).get("items", [])
        pending_pods = []

        for pod in pods:
            if not isinstance(pod, dict):
                continue

            status = pod.get("status", {})
            phase = status.get("phase", "")
            if phase.lower() == "pending":
                metadata = pod.get("metadata", {})
                name = metadata.get("name", "unknown")
                namespace = metadata.get("namespace", "default")

                # Store last known namespace for future queries
                st.session_state.last_known_namespace = namespace

                describe_response = call_tool(server_url, "kubectl_describe", {
                    "resourceType": "pod",
                    "name": name,
                    "namespace": namespace
                })

                reason = "Unknown reason. Use `kubectl describe pod` for details."
                if not describe_response.get("error"):
                    describe_text = describe_response.get("result", "")
                    if isinstance(describe_text, str):
                        events_start = describe_text.find("Events:")
                        if events_start != -1:
                            events_text = describe_text[events_start:]
                            lines = events_text.splitlines()
                            for line in lines[2:]:
                                if "Warning" in line or "Failed" in line or "Insufficient" in line:
                                    reason = line.strip()
                                    break
                        else:
                            if "Insufficient cpu" in describe_text:
                                reason = "Insufficient CPU resources in cluster."
                            elif "Insufficient memory" in describe_text:
                                reason = "Insufficient memory resources in cluster."
                            elif "node(s) had taint" in describe_text:
                                reason = "Node taints prevent scheduling. Check tolerations."
                            elif "Failed to pull image" in describe_text:
                                reason = "Image pull failure. Check image name or registry access."
                            elif "node(s) didn't match Pod's node affinity" in describe_text:
                                reason = "Pod has node affinity rules that no node satisfies."

                pending_pods.append({
                    "name": name,
                    "namespace": namespace,
                    "reason": reason
                })

        return pending_pods

    except Exception as e:
        return [{"error": f"Exception while checking pending pods: {str(e)}"}]

# ---------------- GEMINI FUNCTIONS ----------------
def ask_gemini_for_tool_decision(query: str, server_url: str):
    tools = list_mcp_tools(server_url)
    tool_names = [t["name"] for t in tools if "name" in t]

    context_notes = ""
    if st.session_state.last_known_cluster_name:
        context_notes += f"\nUser previously interacted with cluster: {st.session_state.last_known_cluster_name}"
    if st.session_state.last_known_cluster_size:
        context_notes += f"\nLast known cluster size: {st.session_state.last_known_cluster_size} nodes"
    if st.session_state.last_known_namespace:
        context_notes += f"\nLast used namespace: {st.session_state.last_known_namespace}"

    instruction = f"""
You are an AI agent that maps user queries to MCP tools.
User query: "{query}"
{context_notes}

Available tools: {json.dumps(tool_names, indent=2)}

Rules:
- If user says "describe pod X [in ns Y]" → use tool: kubectl_describe, args: {{"name": "X", "namespace": "Y" or last_known_namespace or "default"}}
- If user asks about "pending pods" → use custom_pending_pods_check
- If about errors → kubectl_get with resourceType=events, allNamespaces=true
- Extract namespace from query if possible.
- If unsure → tool=null, args=null.

Respond ONLY in strict JSON:
{{"tool": "<tool_name>" | null, "args": {{}} | null, "explanation": "Short explanation"}}
"""
    if not GEMINI_AVAILABLE:
        query_lower = query.lower()
        if "describe pod" in query_lower:
            # Extract pod name and namespace
            parts = query.split()
            if len(parts) >= 3:
                name = parts[2]
                namespace = st.session_state.get("last_known_namespace", "default")
                # Try to extract namespace from query
                for i, part in enumerate(parts):
                    if part in ["in", "ns", "namespace", "-n"] and i+1 < len(parts):
                        namespace = parts[i+1]
                        break
                return {
                    "tool": "kubectl_describe",
                    "args": {"resourceType": "pod", "name": name, "namespace": namespace},
                    "explanation": f"Describing pod {name} in namespace {namespace}"
                }
        if "pending" in query_lower and ("pod" in query_lower or "pods" in query_lower):
            return {
                "tool": "custom_pending_pods_check",
                "args": {},
                "explanation": "User wants to see pending pods and reasons — checking all pods and describing pending ones."
            }
        if "error" in query_lower or "issue" in query_lower or "problem" in query_lower:
            return {
                "tool": "kubectl_get",
                "args": {"resourceType": "events", "allNamespaces": True},
                "explanation": "User wants to see errors/issues in cluster - checking events"
            }
        if "pods" in query_lower and "efk" in query_lower:
            return {
                "tool": "kubectl_get",
                "args": {"resourceType": "pods", "namespace": "efk"},
                "explanation": "User wants to see pods in efk namespace"
            }
        if "pods" in query_lower:
            return {
                "tool": "kubectl_get",
                "args": {"resourceType": "pods", "allNamespaces": True},
                "explanation": "User wants to see all pods"
            }
        else:
            return {"tool": None, "args": None, "explanation": "Gemini not configured; fallback to chat reply."}
    
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(instruction)
        text = response.text.strip()
        
        parsed = None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = _extract_json_from_text(text)
        
        if not parsed:
            parsed = {"tool": None, "args": None, "explanation": f"Gemini invalid response: {text}"}
        
        # Pass user query to sanitize_args for namespace extraction
        parsed["args"] = sanitize_args(parsed.get("args") or {}, query)
        return parsed
        
    except Exception as e:
        return {"tool": None, "args": None, "explanation": f"Gemini error: {str(e)}"}

def ask_gemini_answer(user_input: str, raw_response: dict) -> str:
    if not GEMINI_AVAILABLE:
        return generate_fallback_answer(user_input, raw_response)

    try:
        context_notes = ""
        if st.session_state.last_known_cluster_name:
            context_notes += f"\nPreviously known cluster: {st.session_state.last_known_cluster_name}"
        if st.session_state.last_known_cluster_size:
            context_notes += f"\nPreviously known size: {st.session_state.last_known_cluster_size} nodes"
        if st.session_state.last_known_namespace:
            context_notes += f"\nLast used namespace: {st.session_state.last_known_namespace}"

        model = genai.GenerativeModel(GEMINI_MODEL)
        prompt = (
            f"User asked: {user_input}\n"
            f"Context: {context_notes}\n\n"
            f"Raw system response:\n{json.dumps(raw_response, indent=2)}\n\n"
            "INSTRUCTIONS:\n"
            "- Respond in clear, natural, conversational English.\n"
            "- If describing a pod, show Events section if available.\n"
            "- If pending pods → show name, namespace, reason, and fix advice.\n"
            "- If pod not found → say 'Pod not found' and suggest checking name/namespace.\n"
            "- NEVER show raw JSON or errors unless asked.\n"
            "- Be helpful, precise, and friendly."
        )
        
        resp = model.generate_content(prompt)
        answer = getattr(resp, "text", str(resp)).strip()

        extract_and_store_cluster_info(user_input, answer)

        return answer

    except Exception as e:
        return generate_fallback_answer(user_input, raw_response)

def generate_fallback_answer(user_input: str, raw_response: dict) -> str:
    if "error" in raw_response:
        error_msg = raw_response["error"]
        if "not found" in error_msg.lower():
            return "Pod or resource not found. Please double-check the name and namespace. You can list pods with `kubectl get pods -n <namespace>`."
        return f"Sorry, I encountered an issue: {error_msg}"

    result = raw_response.get("result", {})

    # Handle pending pods
    if isinstance(result, list) and len(result) > 0 and "name" in result[0] and "reason" in result[0]:
        count = len(result)
        if count == 0:
            return "No pending pods found. All pods are running normally."

        lines = [f"✅ I found {count} pending pod(s):"]
        for pod in result:
            name = pod.get("name", "unknown")
            ns = pod.get("namespace", "default")
            reason = pod.get("reason", "Unknown reason")

            lines.append(f"\n🔹 Pod: `{name}`\n   Namespace: `{ns}`\n   Reason: {reason}")

            fix = ""
            if "Insufficient cpu" in reason:
                fix = "💡 **Fix**: Scale up cluster nodes or reduce CPU `requests` in pod spec."
            elif "Insufficient memory" in reason:
                fix = "💡 **Fix**: Add more memory or reduce memory `requests`."
            elif "node(s) had taint" in reason:
                fix = "💡 **Fix**: Add `tolerations` to pod spec or remove taints from nodes."
            elif "Failed to pull image" in reason:
                fix = "💡 **Fix**: Verify image name/tag and registry access. Does `docker pull` work?"
            elif "node affinity" in reason:
                fix = "💡 **Fix**: Check node labels match pod’s `nodeAffinity` rules."
            else:
                fix = f"💡 **Fix**: Run `kubectl describe pod {name} -n {ns}` for detailed events."

            lines.append(fix)

        return "\n".join(lines)

    # Handle kubectl_describe output
    if isinstance(result, str) and ("Events:" in result or "Name:" in result):
        # Store namespace if found
        ns_match = re.search(r"Namespace:\s*(\S+)", result)
        if ns_match:
            st.session_state.last_known_namespace = ns_match.group(1)

        # Return full describe but clean up
        if "not found" in result.lower():
            return "❌ Pod not found. Please verify the pod name and namespace."

        return f"```\n{result.strip()}\n```"

    # Existing logic for events, nodes, etc.
    if isinstance(result, dict):
        if "items" in result:
            items = result["items"]
            count = len(items)

            if "event" in user_input.lower() or "error" in user_input.lower():
                if items:
                    error_count = 0
                    error_details = []
                    for item in items:
                        if isinstance(item, dict):
                            event_type = item.get('type', '').lower()
                            reason = item.get('reason', '')
                            message = item.get('message', '')
                            namespace = item.get('metadata', {}).get('namespace', 'default')
                            if event_type == 'error' or 'error' in reason.lower() or 'fail' in reason.lower():
                                error_count += 1
                                error_details.append(f"• **{namespace}**: {reason} - {message}")
                    if error_count > 0:
                        return f"⚠️ Found {error_count} error events:\n\n" + "\n".join(error_details)
                    else:
                        return "✅ No error events found. Cluster is healthy!"
                else:
                    return "No events found."

            if "pod" in user_input.lower() and isinstance(items, list):
                pods = []
                for item in items:
                    if isinstance(item, dict):
                        name = item.get("metadata", {}).get("name", "unnamed")
                        ns = item.get("metadata", {}).get("namespace", "default")
                        phase = item.get("status", {}).get("phase", "Unknown")
                        pods.append(f"• `{name}` in `{ns}` → **{phase}**")
                        if phase == "Pending":
                            st.session_state.last_known_namespace = ns  # Remember for next query
                if pods:
                    return f"📋 Found {count} pod(s):\n" + "\n".join(pods)
                else:
                    return "No pods found."

    if result:
        return f"✅ Operation completed: {json.dumps(result, indent=2)}"

    return "✅ Done."

def extract_and_store_cluster_info(user_input: str, answer: str):
    try:
        if "cluster name" in user_input.lower():
            patterns = [
                r"cluster[^\w]*([\w-]+)",
                r"name[^\w][:\-]?[^\w]([\w-]+)",
                r"\*([\w-]+)\*",
            ]
            for pattern in patterns:
                match = re.search(pattern, answer, re.IGNORECASE)
                if match:
                    cluster_name = match.group(1).strip()
                    st.session_state.last_known_cluster_name = cluster_name
                    break

        if "cluster size" in user_input.lower() or "how many nodes" in user_input.lower():
            numbers = re.findall(r'\b\d+\b', answer)
            if numbers:
                st.session_state.last_known_cluster_size = int(numbers[0])

        if "namespace" in user_input.lower():
            ns_match = re.search(r"namespace[^\w]*([\w-]+)", answer, re.IGNORECASE)
            if ns_match:
                st.session_state.last_known_namespace = ns_match.group(1).strip()

    except Exception:
        pass

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
            st.session_state.last_known_namespace = None
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

    with st.spinner("🤔 Analyzing your request..."):
        decision = ask_gemini_for_tool_decision(user_prompt, selected_server["url"])

    explanation = decision.get("explanation", "I'm figuring out how to help you...")
    st.session_state.messages.append({"role": "assistant", "content": f"💡 {explanation}"})
    with st.chat_message("assistant"):
        st.markdown(f"💡 {explanation}")

    tool_name = decision.get("tool")
    tool_args = decision.get("args") or {}

    if tool_name:
        with st.chat_message("assistant"):
            st.markdown(f"🔧 Executing `{tool_name}`...")

        if tool_name == "custom_pending_pods_check":
            with st.spinner("🔍 Scanning for pending pods and reasons..."):
                pending_pods = get_pending_pods_with_reason(selected_server["url"])
                resp = {"result": pending_pods}
        elif ("error" in user_prompt.lower() or "issue" in user_prompt.lower() or 
              "problem" in user_prompt.lower() or "show me any errors" in user_prompt.lower()):
            with st.spinner("🔍 Scanning for errors in cluster events..."):
                error_events = get_cluster_events_with_errors(selected_server["url"])
                resp = {"result": {"items": error_events}}
        elif (user_prompt.lower().strip() in ["show me all resources in cluster", "get all resources", "all resources"] or
              ("all" in user_prompt.lower() and "resource" in user_prompt.lower())):
            with st.spinner("🔄 Gathering all cluster resources..."):
                all_resources = get_all_cluster_resources(selected_server["url"])
                resp = {"result": all_resources}
        else:
            with st.spinner("🔄 Processing your request..."):
                resp = call_tool(selected_server["url"], tool_name, tool_args)

        with st.spinner("📝 Formatting response..."):
            final_answer = ask_gemini_answer(user_prompt, resp)

        st.session_state.messages.append({"role": "assistant", "content": final_answer})
        with st.chat_message("assistant"):
            st.markdown(final_answer)

    else:
        helpful_response = (
            "I couldn't find a specific tool to answer your question. Here are some things you can try:\n\n"
            "**For Kubernetes:**\n"
            "- \"List all namespaces\"\n"
            "- \"Show running pods\"\n"
            "- \"Get cluster nodes\"\n"
            "- \"Show all services\"\n"
            "- \"List all secrets\"\n"
            "- \"Show errors in cluster\"\n"
            "- \"Check cluster events\"\n"
            "- \"Show all resources in cluster\"\n"
            "- \"Show pending pods and reasons\"\n"
            "- \"Describe pod <name> in namespace <ns>\"\n\n"
            "**Examples:**\n"
            "  • `describe pod my-pod dev`\n"
            "  • `show pods in efk namespace`\n"
            "  • `kubectl describe pod my-pod -n prod`\n\n"
            "**For Jenkins/ArgoCD:**\n"
            "- \"List all jobs\"\n"
            "- \"List applications\"\n\n"
            "Or try being more specific!"
        )
        
        st.session_state.messages.append({"role": "assistant", "content": helpful_response})
        with st.chat_message("assistant"):
            st.markdown(helpful_response)

if __name__ == "__main__":
    main()
