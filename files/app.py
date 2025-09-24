import os
import json
import time
import requests
import streamlit as st
from dotenv import load_dotenv
import google.generativeai as genai
from typing import Optional, Dict, Any, List
import re
from datetime import datetime, timedelta

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
    st.session_state.cluster_issues = []
    st.session_state.fix_suggestions = {}

# ---------------- CLUSTER ISSUE DETECTION ----------------
class ClusterIssueDetector:
    def __init__(self, server_url: str):
        self.server_url = server_url
    
    def detect_all_issues(self) -> List[Dict[str, Any]]:
        """Comprehensive cluster issue detection"""
        issues = []
        
        # Check nodes
        issues.extend(self._check_nodes())
        
        # Check pods
        issues.extend(self._check_pods())
        
        # Check resources
        issues.extend(self._check_resources())
        
        # Check services
        issues.extend(self._check_services())
        
        # Check events for errors
        issues.extend(self._check_events())
        
        return issues
    
    def _check_nodes(self) -> List[Dict[str, Any]]:
        """Check node health and resources"""
        issues = []
        try:
            response = call_tool(self.server_url, "kubectl_get", {
                "resourceType": "nodes"
            })
            
            if response and not response.get("error"):
                nodes = response.get("result", {}).get("items", [])
                
                for node in nodes:
                    node_name = node.get("metadata", {}).get("name", "unknown")
                    
                    # Check node conditions
                    conditions = node.get("status", {}).get("conditions", [])
                    for condition in conditions:
                        if condition.get("type") == "Ready" and condition.get("status") != "True":
                            issues.append({
                                "type": "NODE_ISSUE",
                                "severity": "HIGH",
                                "resource": f"Node/{node_name}",
                                "message": f"Node {node_name} is not ready: {condition.get('message', 'Unknown reason')}",
                                "fix_suggestion": f"Check node {node_name} status and restart if necessary"
                            })
                    
                    # Check resource pressure
                    for condition in conditions:
                        if condition.get("type") in ["MemoryPressure", "DiskPressure", "PIDPressure"]:
                            if condition.get("status") == "True":
                                issues.append({
                                    "type": "RESOURCE_PRESSURE",
                                    "severity": "MEDIUM",
                                    "resource": f"Node/{node_name}",
                                    "message": f"Node {node_name} has {condition.get('type')}",
                                    "fix_suggestion": f"Free up resources on node {node_name} or add more capacity"
                                })
        
        except Exception as e:
            issues.append({
                "type": "DETECTION_ERROR",
                "severity": "LOW",
                "resource": "Nodes",
                "message": f"Failed to check nodes: {str(e)}",
                "fix_suggestion": "Check MCP server connectivity"
            })
        
        return issues
    
    def _check_pods(self) -> List[Dict[str, Any]]:
        """Check pod health across all namespaces"""
        issues = []
        try:
            response = call_tool(self.server_url, "kubectl_get", {
                "resourceType": "pods",
                "allNamespaces": True
            })
            
            if response and not response.get("error"):
                pods = response.get("result", {}).get("items", [])
                
                for pod in pods:
                    pod_name = pod.get("metadata", {}).get("name", "unknown")
                    namespace = pod.get("metadata", {}).get("namespace", "default")
                    status = pod.get("status", {})
                    phase = status.get("phase", "Unknown")
                    
                    # Check pod phase
                    if phase == "Pending":
                        issues.append({
                            "type": "POD_ISSUE",
                            "severity": "MEDIUM",
                            "resource": f"Pod/{namespace}/{pod_name}",
                            "message": f"Pod {pod_name} in namespace {namespace} is stuck in Pending state",
                            "fix_suggestion": f"Check resource availability and constraints for pod {pod_name}"
                        })
                    
                    elif phase == "Failed":
                        issues.append({
                            "type": "POD_ISSUE",
                            "severity": "HIGH",
                            "resource": f"Pod/{namespace}/{pod_name}",
                            "message": f"Pod {pod_name} in namespace {namespace} has failed",
                            "fix_suggestion": f"Check logs and restart pod {pod_name} in namespace {namespace}"
                        })
                    
                    # Check container statuses
                    container_statuses = status.get("containerStatuses", [])
                    for cs in container_statuses:
                        if cs.get("ready") == False:
                            issues.append({
                                "type": "CONTAINER_ISSUE",
                                "severity": "MEDIUM",
                                "resource": f"Pod/{namespace}/{pod_name}",
                                "message": f"Container {cs.get('name')} in pod {pod_name} is not ready",
                                "fix_suggestion": f"Check container logs and configuration"
                            })
                        
                        # Check restart counts
                        restart_count = cs.get("restartCount", 0)
                        if restart_count > 10:
                            issues.append({
                                "type": "RESTART_LOOP",
                                "severity": "HIGH",
                                "resource": f"Pod/{namespace}/{pod_name}",
                                "message": f"Container {cs.get('name')} in pod {pod_name} has restarted {restart_count} times",
                                "fix_suggestion": f"Investigate why container is crashing frequently"
                            })
        
        except Exception as e:
            issues.append({
                "type": "DETECTION_ERROR",
                "severity": "LOW",
                "resource": "Pods",
                "message": f"Failed to check pods: {str(e)}",
                "fix_suggestion": "Check MCP server connectivity"
            })
        
        return issues
    
    def _check_resources(self) -> List[Dict[str, Any]]:
        """Check resource utilization and quotas"""
        issues = []
        try:
            # Check persistent volumes
            pv_response = call_tool(self.server_url, "kubectl_get", {
                "resourceType": "persistentvolumes"
            })
            
            if pv_response and not pv_response.get("error"):
                pvs = pv_response.get("result", {}).get("items", [])
                for pv in pvs:
                    pv_name = pv.get("metadata", {}).get("name", "unknown")
                    status = pv.get("status", {}).get("phase", "Unknown")
                    if status == "Failed":
                        issues.append({
                            "type": "STORAGE_ISSUE",
                            "severity": "HIGH",
                            "resource": f"PV/{pv_name}",
                            "message": f"Persistent Volume {pv_name} is in Failed state",
                            "fix_suggestion": f"Check storage configuration for PV {pv_name}"
                        })
            
            # Check persistent volume claims
            pvc_response = call_tool(self.server_url, "kubectl_get", {
                "resourceType": "persistentvolumeclaims",
                "allNamespaces": True
            })
            
            if pvc_response and not pvc_response.get("error"):
                pvcs = pvc_response.get("result", {}).get("items", [])
                for pvc in pvcs:
                    pvc_name = pvc.get("metadata", {}).get("name", "unknown")
                    namespace = pvc.get("metadata", {}).get("namespace", "default")
                    status = pvc.get("status", {}).get("phase", "Unknown")
                    if status == "Pending":
                        issues.append({
                            "type": "STORAGE_ISSUE",
                            "severity": "MEDIUM",
                            "resource": f"PVC/{namespace}/{pvc_name}",
                            "message": f"PVC {pvc_name} in namespace {namespace} is pending",
                            "fix_suggestion": f"Check available storage and storage class for PVC {pvc_name}"
                        })
        
        except Exception as e:
            issues.append({
                "type": "DETECTION_ERROR",
                "severity": "LOW",
                "resource": "Resources",
                "message": f"Failed to check resources: {str(e)}",
                "fix_suggestion": "Check MCP server connectivity"
            })
        
        return issues
    
    def _check_services(self) -> List[Dict[str, Any]]:
        """Check service endpoints"""
        issues = []
        try:
            response = call_tool(self.server_url, "kubectl_get", {
                "resourceType": "services",
                "allNamespaces": True
            })
            
            if response and not response.get("error"):
                services = response.get("result", {}).get("items", [])
                
                for service in services:
                    service_name = service.get("metadata", {}).get("name", "unknown")
                    namespace = service.get("metadata", {}).get("namespace", "default")
                    
                    # Check if service has endpoints
                    endpoints_response = call_tool(self.server_url, "kubectl_get", {
                        "resourceType": "endpoints",
                        "name": service_name,
                        "namespace": namespace
                    })
                    
                    if endpoints_response and not endpoints_response.get("error"):
                        endpoints = endpoints_response.get("result", {})
                        subsets = endpoints.get("subsets", [])
                        if not subsets:
                            issues.append({
                                "type": "SERVICE_ISSUE",
                                "severity": "MEDIUM",
                                "resource": f"Service/{namespace}/{service_name}",
                                "message": f"Service {service_name} in namespace {namespace} has no endpoints",
                                "fix_suggestion": f"Check selector labels and pod availability for service {service_name}"
                            })
        
        except Exception as e:
            issues.append({
                "type": "DETECTION_ERROR",
                "severity": "LOW",
                "resource": "Services",
                "message": f"Failed to check services: {str(e)}",
                "fix_suggestion": "Check MCP server connectivity"
            })
        
        return issues
    
    def _check_events(self) -> List[Dict[str, Any]]:
        """Check recent events for errors"""
        issues = []
        try:
            # Get events from all namespaces
            response = call_tool(self.server_url, "kubectl_get", {
                "resourceType": "events",
                "allNamespaces": True
            })
            
            if response and not response.get("error"):
                events = response.get("result", {}).get("items", [])
                
                # Filter recent events (last 1 hour) with error types
                recent_events = []
                for event in events:
                    event_time = event.get("lastTimestamp", "")
                    if is_recent_event(event_time):
                        if event.get("type") == "Warning" or "error" in event.get("message", "").lower():
                            recent_events.append(event)
                
                for event in recent_events[:10]:  # Limit to 10 most recent errors
                    issues.append({
                        "type": "EVENT_WARNING",
                        "severity": "LOW",
                        "resource": f"Event/{event.get('involvedObject', {}).get('kind', 'Unknown')}",
                        "message": f"{event.get('reason', 'Unknown')}: {event.get('message', 'No message')}",
                        "fix_suggestion": "Investigate the event details for root cause"
                    })
        
        except Exception as e:
            # Events might not be accessible, skip silently
            pass
        
        return issues

def is_recent_event(event_time: str) -> bool:
    """Check if event is from last 1 hour"""
    try:
        if not event_time:
            return False
        
        # Parse event time (assuming ISO format)
        event_dt = datetime.fromisoformat(event_time.replace('Z', '+00:00'))
        one_hour_ago = datetime.now().astimezone() - timedelta(hours=1)
        return event_dt > one_hour_ago
    except Exception:
        return False

def generate_fix_commands(issues: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Generate kubectl commands to fix detected issues"""
    fix_commands = {}
    
    for issue in issues:
        resource = issue.get("resource", "")
        fix_suggestion = issue.get("fix_suggestion", "")
        commands = []
        
        if "Pod" in resource:
            # Extract namespace and pod name
            parts = resource.split('/')
            if len(parts) >= 3:
                namespace = parts[1]
                pod_name = parts[2]
                
                commands.append(f"kubectl describe pod {pod_name} -n {namespace}")
                commands.append(f"kubectl logs {pod_name} -n {namespace} --all-containers=true")
                commands.append(f"kubectl delete pod {pod_name} -n {namespace} # Restart pod")
        
        elif "Node" in resource:
            node_name = resource.split('/')[-1]
            commands.append(f"kubectl describe node {node_name}")
            commands.append(f"kubectl drain {node_name} --ignore-daemonsets # Maintenance")
            commands.append(f"kubectl uncordon {node_name} # After maintenance")
        
        elif "Service" in resource:
            parts = resource.split('/')
            if len(parts) >= 3:
                namespace = parts[1]
                service_name = parts[2]
                commands.append(f"kubectl describe service {service_name} -n {namespace}")
                commands.append(f"kubectl get endpoints {service_name} -n {namespace}")
        
        if commands:
            fix_commands[resource] = commands
    
    return fix_commands

# ---------------- EXISTING HELPERS (with minor enhancements) ----------------
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

def detect_server_from_query(query: str, available_servers: list) -> Optional[Dict[str, Any]]:
    """Automatically detect which server to use based on query content."""
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
                 "resource" in query_lower or "issue" in query_lower or
                 "problem" in query_lower or "error" in query_lower or
                 "crash" in query_lower or "pending" in query_lower) and 
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

def ask_gemini_for_tool_decision(query: str, server_url: str):
    """Use Gemini to map user query -> MCP tool + arguments."""
    tools = list_mcp_tools(server_url)
    tool_names = [t["name"] for t in tools if "name" in t]

    context_notes = ""
    if st.session_state.last_known_cluster_name:
        context_notes += f"\nUser previously interacted with cluster: {st.session_state.last_known_cluster_name}"
    if st.session_state.last_known_cluster_size:
        context_notes += f"\nLast known cluster size: {st.session_state.last_known_cluster_size} nodes"

    # Enhanced instruction for issue detection
    instruction = f"""
You are an AI agent that maps user queries to MCP tools.
User query: "{query}"
{context_notes}

Available tools in this MCP server: {json.dumps(tool_names, indent=2)}

Rules:
- If user asks about cluster issues, problems, errors, crashes, pending states, or health checks, use appropriate kubectl commands
- For comprehensive cluster health check, suggest multiple resource checks
- If user wants to see ALL pods (not just default), set allNamespaces=true
- If user wants ALL resources in cluster, check nodes, pods, services, pvcs, events
- Only choose from the tools above.

Respond ONLY in strict JSON:
{{"tool": "<tool_name>" | null, "args": {{}} | null, "explanation": "Short explanation"}}
"""
    if not GEMINI_AVAILABLE:
        query_lower = query.lower()
        if any(word in query_lower for word in ["issue", "problem", "error", "crash", "pending", "health", "check"]):
            return {
                "tool": "kubectl_get",
                "args": {"resourceType": "nodes"},
                "explanation": "User wants cluster health check - starting with nodes"
            }
        elif "all pods" in query_lower and "default" not in query_lower:
            return {
                "tool": "kubectl_get",
                "args": {"resourceType": "pods", "allNamespaces": True},
                "explanation": "User wants all pods across all namespaces"
            }
        elif "all resources" in query_lower or "everything" in query_lower:
            return {
                "tool": "kubectl_get",
                "args": {"resourceType": "nodes"},
                "explanation": "User wants all cluster resources - starting comprehensive check"
            }
        elif "pods" in query_lower:
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
        
        parsed = _extract_json_from_text(text)
        if not parsed:
            parsed = {"tool": None, "args": None, "explanation": f"Gemini invalid response: {text}"}
        
        return parsed
        
    except Exception as e:
        return {"tool": None, "args": None, "explanation": f"Gemini error: {str(e)}"}

def _extract_json_from_text(text: str) -> Optional[dict]:
    """Extract JSON object from free text."""
    try:
        start = text.find('{')
        end = text.rfind('}') + 1
        if start != -1 and end != -1 and end > start:
            json_str = text[start:end]
            return json.loads(json_str)
    except Exception:
        pass
    return None

def ask_gemini_answer(user_input: str, raw_response: dict) -> str:
    """Use Gemini to convert raw MCP response into human-friendly answer."""
    if not GEMINI_AVAILABLE:
        return generate_fallback_answer(user_input, raw_response)

    try:
        context_notes = ""
        if st.session_state.last_known_cluster_name:
            context_notes += f"\nPreviously known cluster: {st.session_state.last_known_cluster_name}"
        if st.session_state.last_known_cluster_size:
            context_notes += f"\nPreviously known size: {st.session_state.last_known_cluster_size} nodes"

        model = genai.GenerativeModel(GEMINI_MODEL)
        prompt = (
            f"User asked: {user_input}\n"
            f"Context: {context_notes}\n\n"
            f"Raw system response:\n{json.dumps(raw_response, indent=2)}\n\n"
            "INSTRUCTIONS:\n"
            "- Respond in clear, natural English\n"
            "- If issues found, list them clearly with severity\n"
            "- Suggest fixes for each issue\n"
            "- Format with bullet points for lists\n"
            "- If no issues found, say 'No issues detected in cluster'\n"
            "- Be helpful and technical but easy to understand"
        )
        
        resp = model.generate_content(prompt)
        answer = getattr(resp, "text", str(resp)).strip()

        extract_and_store_cluster_info(user_input, answer)

        return answer

    except Exception as e:
        return generate_fallback_answer(user_input, raw_response)

def generate_fallback_answer(user_input: str, raw_response: dict) -> str:
    """Generate human-friendly answer without Gemini."""
    if "error" in raw_response:
        return f"❌ Error: {raw_response['error']}"
    
    result = raw_response.get("result", {})
    
    if isinstance(result, dict) and "items" in result:
        items = result["items"]
        count = len(items)
        
        if "node" in user_input.lower():
            node_info = []
            for item in items:
                name = item.get("metadata", {}).get("name", "unknown")
                status = "Ready" if any(c.get("type") == "Ready" and c.get("status") == "True" 
                                      for c in item.get("status", {}).get("conditions", [])) else "Not Ready"
                node_info.append(f"{name} ({status})")
            
            return f"**Cluster Nodes ({count}):**\n" + "\n".join([f"• {info}" for info in node_info])
    
    return f"✅ Operation completed. Found {len(result.get('items', []))} items."

def extract_and_store_cluster_info(user_input: str, answer: str):
    """Extract cluster info from responses."""
    try:
        if "cluster" in user_input.lower():
            # Extract cluster name
            name_match = re.search(r"cluster[^\w]*([\w-]+)", answer, re.IGNORECASE)
            if name_match:
                st.session_state.last_known_cluster_name = name_match.group(1).strip()
            
            # Extract node count
            count_match = re.search(r"(\d+)\s*nodes?", answer, re.IGNORECASE)
            if count_match:
                st.session_state.last_known_cluster_size = int(count_match.group(1))
    except Exception:
        pass

# ---------------- STREAMLIT APP ----------------
def main():
    st.set_page_config(page_title="MCP Cluster Health Bot", page_icon="⚡", layout="wide")
    st.title("🤖 MaSaOps Cluster Health Bot")

    # Sidebar with enhanced controls
    with st.sidebar:
        st.header("⚙️ Cluster Health Settings")
        
        if st.button("🔄 Discover Available Servers"):
            with st.spinner("Discovering MCP servers..."):
                servers = load_servers()
                st.session_state.available_servers = servers
                st.success(f"Found {len(servers)} servers")
        
        st.subheader("🔍 Health Check Options")
        auto_fix = st.checkbox("Auto-generate fix commands", value=True)
        comprehensive_check = st.checkbox("Comprehensive health check", value=True)
        
        if st.button("🚨 Run Cluster Health Scan"):
            if st.session_state.available_servers:
                selected_server = detect_server_from_query("cluster health check issues problems", 
                                                         st.session_state.available_servers)
                if selected_server:
                    with st.spinner("🔍 Scanning cluster for issues..."):
                        detector = ClusterIssueDetector(selected_server["url"])
                        issues = detector.detect_all_issues()
                        st.session_state.cluster_issues = issues
                        
                        if auto_fix:
                            st.session_state.fix_suggestions = generate_fix_commands(issues)
                    
                    st.success(f"Found {len(issues)} issues")
                    for issue in issues:
                        st.error(f"**{issue['severity']}**: {issue['message']}")
        
        if st.button("🗑️ Clear Chat History"):
            st.session_state.messages = []
            st.session_state.cluster_issues = []
            st.session_state.fix_suggestions = {}
            st.rerun()

    # Main chat interface
    st.subheader("🔍 What's happening in your cluster today?")
    
    # Display health status
    if st.session_state.cluster_issues:
        st.error(f"🚨 **Cluster Health Status**: {len(st.session_state.cluster_issues)} issues detected")
    else:
        st.success("✅ **Cluster Health Status**: No known issues")
    
    # Display chat history
    for msg in st.session_state.messages:
        role = msg.get("role", "assistant")
        with st.chat_message(role):
            st.markdown(msg.get("content", ""))
    
    # Chat input
    user_prompt = st.chat_input("Ask about cluster issues, pods, nodes, or resources...")
    if not user_prompt:
        return
    
    # Add user message to history
    st.session_state.messages.append({"role": "user", "content": user_prompt})
    with st.chat_message("user"):
        st.markdown(user_prompt)
    
    # Auto-detect server
    with st.spinner("🔍 Finding the right server..."):
        selected_server = detect_server_from_query(user_prompt, st.session_state.available_servers)
    
    if not selected_server:
        error_msg = "❌ No MCP servers available. Please check your servers.json file."
        st.session_state.messages.append({"role": "assistant", "content": error_msg})
        with st.chat_message("assistant"):
            st.error(error_msg)
        return
    
    # Show server info
    server_info = f"🤖 Using server: **{selected_server['name']}**"
    st.session_state.messages.append({"role": "assistant", "content": server_info})
    with st.chat_message("assistant"):
        st.markdown(server_info)
    
    # Handle special cases for cluster health and issue detection
    user_lower = user_prompt.lower()
    
    if any(word in user_lower for word in ["issue", "problem", "error", "crash", "pending", "health", "check", "fix"]):
        # Run comprehensive health check
        with st.spinner("🔍 Scanning cluster for issues..."):
            detector = ClusterIssueDetector(selected_server["url"])
            issues = detector.detect_all_issues()
            st.session_state.cluster_issues = issues
            
            if auto_fix:
                st.session_state.fix_suggestions = generate_fix_commands(issues)
        
        # Generate response
        if issues:
            response_lines = ["🚨 **Cluster Issues Found:**\n"]
            
            # Group by severity
            high_issues = [i for i in issues if i["severity"] == "HIGH"]
            medium_issues = [i for i in issues if i["severity"] == "MEDIUM"]
            low_issues = [i for i in issues if i["severity"] == "LOW"]
            
            if high_issues:
                response_lines.append("\n🔴 **High Severity Issues:**")
                for issue in high_issues:
                    response_lines.append(f"• {issue['message']}")
            
            if medium_issues:
                response_lines.append("\n🟡 **Medium Severity Issues:**")
                for issue in medium_issues:
                    response_lines.append(f"• {issue['message']")
            
            if low_issues:
                response_lines.append("\n🔵 **Low Severity Issues:**")
                for issue in low_issues:
                    response_lines.append(f"• {issue['message']")
            
            # Add fix suggestions
            if st.session_state.fix_suggestions:
                response_lines.append("\n🛠️ **Fix Suggestions:**")
                for resource, commands in st.session_state.fix_suggestions.items():
                    response_lines.append(f"\n**{resource}:**")
                    for cmd in commands:
                        response_lines.append(f"`{cmd}`")
            
            final_response = "\n".join(response_lines)
        else:
            final_response = "✅ **No issues detected in the cluster!** Everything looks healthy."
        
        st.session_state.messages.append({"role": "assistant", "content": final_response})
        with st.chat_message("assistant"):
            st.markdown(final_response)
        
        return
    
    # Normal tool execution flow
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
        
        # Execute tool
        with st.spinner("🔄 Processing your request..."):
            resp = call_tool(selected_server["url"], tool_name, tool_args)
        
        # Generate response
        with st.spinner("📝 Formatting response..."):
            final_answer = ask_gemini_answer(user_prompt, resp)
        
        st.session_state.messages.append({"role": "assistant", "content": final_answer})
        with st.chat_message("assistant"):
            st.markdown(final_answer)
    
    else:
        # Helpful suggestions
        helpful_response = """
I can help you with various cluster operations. Here are some examples:

**🔍 Health & Issues:**
- "Check cluster health"
- "Find pod crashes"
- "Show pending resources"
- "Check node status"

**📊 Resources:**
- "Show all pods in all namespaces"
- "List all services"
- "Check persistent volumes"
- "Show cluster nodes"

**🔧 Troubleshooting:**
- "Why are pods crashing?"
- "Check service endpoints"
- "Investigate resource issues"

Try asking about specific resources or health checks!
"""
        
        st.session_state.messages.append({"role": "assistant", "content": helpful_response})
        with st.chat_message("assistant"):
            st.markdown(helpful_response)

if __name__ == "__main__":
    main()
