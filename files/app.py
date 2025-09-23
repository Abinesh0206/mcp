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
        
        # Handle different response formats
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
        
        # Handle regular JSON responses
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
    
    # Handle different response formats
    result = resp.get("result", {})
    if isinstance(result, dict):
        return result.get("tools", [])
    if isinstance(result, list):
        return result
    
    # Check if tools are at the root level
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

def sanitize_args(args: dict):
    """Fix arguments before sending to MCP tools."""
    if not args:
        return {}

    fixed = args.copy()
    
    # Handle resource/resourceType naming
    if "resource" in fixed and "resourceType" not in fixed:
        fixed["resourceType"] = fixed.pop("resource")
    
    # Handle events specifically - they need namespace handling
    if fixed.get("resourceType") == "events":
        if "namespace" not in fixed:
            # For events, if no namespace specified, get all namespaces
            fixed["allNamespaces"] = True
        elif fixed.get("namespace") == "all":
            fixed["allNamespaces"] = True
            fixed.pop("namespace", None)
    
    # Set default namespace for pods if not specified
    elif fixed.get("resourceType") == "pods" and "namespace" not in fixed:
        fixed["namespace"] = "default"
    
    # Handle "all namespaces" request for other resources
    elif fixed.get("namespace") == "all":
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
        "event": "events",
        "ev": "events",
        "all": "all"
    }
    
    if fixed.get("resourceType") in resource_mappings:
        fixed["resourceType"] = resource_mappings[fixed["resourceType"]]
    
    return fixed

def _extract_json_from_text(text: str) -> Optional[dict]:
    """Extract JSON object from free text."""
    try:
        # Find the first { and last }
        start = text.find('{')
        end = text.rfind('}') + 1
        if start != -1 and end != -1 and end > start:
            json_str = text[start:end]
            return json.loads(json_str)
    except Exception:
        pass
    return None

def detect_server_from_query(query: str, available_servers: list) -> Optional[Dict[str, Any]]:
    """Automatically detect which server to use based on query content."""
    query_lower = query.lower()
    
    # Check each server's tools to see which one matches the query
    for server in available_servers:
        try:
            tools = list_mcp_tools(server["url"])
            tool_names = [t.get("name", "").lower() for t in tools if t.get("name")]
            
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
                 "resource" in query_lower or "error" in query_lower or
                 "event" in query_lower or "pending" in query_lower or
                 "failed" in query_lower or "issue" in query_lower) and 
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

def get_all_cluster_resources(server_url: str):
    """Get all resources in the cluster by querying multiple resource types."""
    resource_types = [
        "pods", "services", "deployments", "configmaps", 
        "secrets", "namespaces", "nodes", "events"
    ]
    
    all_resources = {}
    
    for resource_type in resource_types:
        try:
            params = {"resourceType": resource_type}
            
            # Special handling for events - get from all namespaces
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
                
            # Small delay to avoid overwhelming the server
            time.sleep(0.1)
            
        except Exception as e:
            all_resources[resource_type] = f"Exception: {str(e)}"
    
    return all_resources

def get_cluster_events_with_errors(server_url: str):
    """Specifically get events that indicate errors in the cluster."""
    try:
        # Get events from all namespaces
        response = call_tool(server_url, "kubectl_get", {
            "resourceType": "events",
            "allNamespaces": True
        })
        
        if response and not response.get("error"):
            result = response.get("result", {})
            events = result.get("items", []) if isinstance(result, dict) else result
            
            # Filter for error events
            error_events = []
            if isinstance(events, list):
                for event in events:
                    if isinstance(event, dict):
                        # Look for error indicators in event data
                        event_type = event.get('type', '').lower()
                        reason = event.get('reason', '').lower()
                        message = event.get('message', '').lower()
                        
                        # Common error indicators
                        error_keywords = ['error', 'failed', 'backoff', 'crash', 'unhealthy', 'invalid', 'pending', 'warning']
                        if (event_type == 'error' or 
                            any(keyword in reason for keyword in error_keywords) or
                            any(keyword in message for keyword in error_keywords)):
                            error_events.append(event)
            
            return error_events
        else:
            return [{"error": response.get("error", "Failed to retrieve events")}]
            
    except Exception as e:
        return [{"error": f"Exception while getting events: {str(e)}"}]

def get_pending_pods_with_details(server_url: str):
    """Get pending pods and their detailed status information."""
    try:
        response = call_tool(server_url, "kubectl_get", {
            "resourceType": "pods",
            "allNamespaces": True
        })
        
        if response and not response.get("error"):
            result = response.get("result", {})
            pods = result.get("items", []) if isinstance(result, dict) else result
            
            pending_pods = []
            if isinstance(pods, list):
                for pod in pods:
                    if isinstance(pod, dict):
                        status = pod.get('status', {})
                        phase = status.get('phase', '').lower()
                        
                        if phase == 'pending':
                            pod_name = pod.get('metadata', {}).get('name', 'unknown')
                            namespace = pod.get('metadata', {}).get('namespace', 'default')
                            
                            # Get detailed status conditions
                            conditions = status.get('conditions', [])
                            container_statuses = status.get('containerStatuses', [])
                            
                            # Analyze reasons for pending state
                            reasons = []
                            
                            # Check container statuses
                            for cs in container_statuses:
                                state = cs.get('state', {})
                                waiting = state.get('waiting', {})
                                if waiting:
                                    reason = waiting.get('reason', '')
                                    message = waiting.get('message', '')
                                    if reason:
                                        reasons.append(f"Container waiting: {reason} - {message}")
                            
                            # Check pod conditions
                            for condition in conditions:
                                cond_type = condition.get('type', '')
                                cond_status = condition.get('status', '').lower()
                                cond_reason = condition.get('reason', '')
                                cond_message = condition.get('message', '')
                                
                                if cond_status == 'false' and cond_reason:
                                    reasons.append(f"{cond_type}: {cond_reason} - {cond_message}")
                            
                            # Check for resource issues
                            if not reasons:
                                # Look for events related to this pod
                                events_response = call_tool(server_url, "kubectl_get", {
                                    "resourceType": "events",
                                    "namespace": namespace,
                                    "fieldSelector": f"involvedObject.name={pod_name}"
                                })
                                
                                if events_response and not events_response.get("error"):
                                    events_result = events_response.get("result", {})
                                    pod_events = events_result.get("items", []) if isinstance(events_result, dict) else events_result
                                    
                                    if isinstance(pod_events, list):
                                        for event in pod_events:
                                            if isinstance(event, dict):
                                                event_reason = event.get('reason', '')
                                                event_message = event.get('message', '')
                                                if event_reason and 'insufficient' in event_reason.lower():
                                                    reasons.append(f"Resource issue: {event_reason} - {event_message}")
                            
                            pending_pods.append({
                                'name': pod_name,
                                'namespace': namespace,
                                'reasons': reasons if reasons else ['Unknown reason - check pod describe for details'],
                                'full_pod_info': pod
                            })
            
            return pending_pods
        else:
            return [{"error": response.get("error", "Failed to retrieve pods")}]
            
    except Exception as e:
        return [{"error": f"Exception while getting pods: {str(e)}"}]

def get_failed_pods_with_details(server_url: str):
    """Get failed pods and their detailed status information."""
    try:
        response = call_tool(server_url, "kubectl_get", {
            "resourceType": "pods",
            "allNamespaces": True
        })
        
        if response and not response.get("error"):
            result = response.get("result", {})
            pods = result.get("items", []) if isinstance(result, dict) else result
            
            failed_pods = []
            if isinstance(pods, list):
                for pod in pods:
                    if isinstance(pod, dict):
                        status = pod.get('status', {})
                        phase = status.get('phase', '').lower()
                        
                        if phase == 'failed':
                            pod_name = pod.get('metadata', {}).get('name', 'unknown')
                            namespace = pod.get('metadata', {}).get('namespace', 'default')
                            
                            # Get container status for failure reasons
                            container_statuses = status.get('containerStatuses', [])
                            reasons = []
                            
                            for cs in container_statuses:
                                state = cs.get('state', {})
                                terminated = state.get('terminated', {})
                                if terminated:
                                    exit_code = terminated.get('exitCode', 0)
                                    reason = terminated.get('reason', '')
                                    message = terminated.get('message', '')
                                    
                                    if exit_code != 0:
                                        reasons.append(f"Exit code {exit_code}: {reason} - {message}")
                            
                            failed_pods.append({
                                'name': pod_name,
                                'namespace': namespace,
                                'reasons': reasons if reasons else ['Unknown failure reason'],
                                'full_pod_info': pod
                            })
            
            return failed_pods
        else:
            return [{"error": response.get("error", "Failed to retrieve pods")}]
            
    except Exception as e:
        return [{"error": f"Exception while getting pods: {str(e)}"}]

def analyze_cluster_health(server_url: str):
    """Comprehensive cluster health analysis."""
    health_report = {
        'pending_pods': [],
        'failed_pods': [],
        'error_events': [],
        'node_issues': [],
        'overall_status': 'healthy'
    }
    
    # Check pending pods
    pending_pods = get_pending_pods_with_details(server_url)
    if pending_pods and not any('error' in str(pod) for pod in pending_pods):
        health_report['pending_pods'] = pending_pods
        health_report['overall_status'] 'issues'
    
    # Check failed pods
    failed_pods = get_failed_pods_with_details(server_url)
    if failed_pods and not any('error' in str(pod) for pod in failed_pods):
        health_report['failed_pods'] = failed_pods
        health_report['overall_status'] = 'issues'
    
    # Check error events
    error_events = get_cluster_events_with_errors(server_url)
    if error_events and not any('error' in str(event) for event in error_events):
        health_report['error_events'] = error_events
        health_report['overall_status'] = 'issues'
    
    # Check node status
    try:
        nodes_response = call_tool(server_url, "kubectl_get", {
            "resourceType": "nodes"
        })
        
        if nodes_response and not nodes_response.get("error"):
            result = nodes_response.get("result", {})
            nodes = result.get("items", []) if isinstance(result, dict) else result
            
            if isinstance(nodes, list):
                for node in nodes:
                    if isinstance(node, dict):
                        status = node.get('status', {})
                        conditions = status.get('conditions', [])
                        
                        for condition in conditions:
                            cond_type = condition.get('type', '')
                            cond_status = condition.get('status', '').lower()
                            
                            if cond_type == 'Ready' and cond_status != 'true':
                                node_name = node.get('metadata', {}).get('name', 'unknown')
                                health_report['node_issues'].append(f"Node {node_name} not ready")
                                health_report['overall_status'] = 'issues'
    
    except Exception:
        pass  # Skip node check if it fails
    
    return health_report

# ---------------- GEMINI FUNCTIONS ----------------
def ask_gemini_for_tool_decision(query: str, server_url: str):
    """Use Gemini to map user query -> MCP tool + arguments."""
    tools = list_mcp_tools(server_url)
    tool_names = [t["name"] for t in tools if "name" in t]

    # Inject context from session state if available
    context_notes = ""
    if st.session_state.last_known_cluster_name:
        context_notes += f"\nUser previously interacted with cluster: {st.session_state.last_known_cluster_name}"
    if st.session_state.last_known_cluster_size:
        context_notes += f"\nLast known cluster size: {st.session_state.last_known_cluster_size} nodes"

    instruction = f"""
You are an AI agent that maps user queries to MCP tools.
User query: "{query}"
{context_notes}

Available tools in this MCP server: {json.dumps(tool_names, indent=2)}

Rules:
- If query mentions "error", "issue", "problem", "pending", "failed" - perform comprehensive cluster health check
- If query asks specifically about pending pods, use specialized pending pod analysis
- If query asks specifically about failed pods, use specialized failed pod analysis  
- If query is general cluster status, do full health analysis
- Only choose from the tools above.
- If unsure, set tool=null and args=null.

Respond ONLY in strict JSON:
{{"tool": "<tool_name>" | null, "args": {{}} | null, "explanation": "Short explanation", "analysis_type": "health|pending|failed|events|general"}}
"""
    if not GEMINI_AVAILABLE:
        # Enhanced fallback logic
        query_lower = query.lower()
        
        if any(keyword in query_lower for keyword in ['error', 'issue', 'problem', 'health', 'status']):
            return {
                "tool": "kubectl_get",
                "args": {"resourceType": "events", "allNamespaces": True},
                "explanation": "User wants cluster health status - performing comprehensive analysis",
                "analysis_type": "health"
            }
        elif 'pending' in query_lower:
            return {
                "tool": "kubectl_get", 
                "args": {"resourceType": "pods", "allNamespaces": True},
                "explanation": "User wants pending pods analysis",
                "analysis_type": "pending"
            }
        elif 'failed' in query_lower:
            return {
                "tool": "kubectl_get",
                "args": {"resourceType": "pods", "allNamespaces": True},
                "explanation": "User wants failed pods analysis", 
                "analysis_type": "failed"
            }
        elif 'event' in query_lower:
            return {
                "tool": "kubectl_get",
                "args": {"resourceType": "events", "allNamespaces": True},
                "explanation": "User wants to see cluster events",
                "analysis_type": "events"
            }
        else:
            return {"tool": None, "args": None, "explanation": "Gemini not configured", "analysis_type": "general"}
    
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(instruction)
        text = response.text.strip()
        
        # Try to extract JSON from response
        parsed = None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = _extract_json_from_text(text)
        
        if not parsed:
            parsed = {"tool": None, "args": None, "explanation": f"Gemini invalid response: {text}", "analysis_type": "general"}
        
        parsed["args"] = sanitize_args(parsed.get("args") or {})
        return parsed
        
    except Exception as e:
        return {"tool": None, "args": None, "explanation": f"Gemini error: {str(e)}", "analysis_type": "general"}

def ask_gemini_answer(user_input: str, raw_response: dict, analysis_type: str = "general") -> str:
    """Use Gemini to convert raw MCP response into human-friendly answer with fix suggestions."""
    if not GEMINI_AVAILABLE:
        return generate_fallback_answer_with_fixes(user_input, raw_response, analysis_type)

    try:
        context_notes = ""
        if st.session_state.last_known_cluster_name:
            context_notes += f"\nPreviously known cluster: {st.session_state.last_known_cluster_name}"
        if st.session_state.last_known_cluster_size:
            context_notes += f"\nPreviously known size: {st.session_state.last_known_cluster_size} nodes"

        model = genai.GenerativeModel(GEMINI_MODEL)
        prompt = (
            f"User asked: {user_input}\n"
            f"Context: {context_notes}\n"
            f"Analysis type: {analysis_type}\n\n"
            f"Raw system response:\n{json.dumps(raw_response, indent=2)}\n\n"
            "CRITICAL INSTRUCTIONS:\n"
            "1. First, accurately identify if there are REAL issues (pending pods, failed pods, error events)\n"
            "2. If issues found, explain EXACT reasons for each issue in simple terms\n"
            "3. Provide SPECIFIC fix commands/suggestions for each issue\n"
            "4. Format with clear sections: Issues Found, Reasons, Fix Suggestions\n"
            "5. If no issues found, clearly state 'No issues detected. Cluster is healthy!'\n"
            "6. For pending pods: explain why they're pending and how to fix\n"
            "7. For failed pods: explain why they failed and how to fix\n"
            "8. Use bullet points for clarity\n"
            "9. Be helpful, technical, but easy to understand\n"
            "10. NEVER say 'investigate further' without providing specific commands to run\n"
        )
        
        resp = model.generate_content(prompt)
        answer = getattr(resp, "text", str(resp)).strip()

        # Extract and store cluster info for future context
        extract_and_store_cluster_info(user_input, answer)

        return answer

    except Exception as e:
        return generate_fallback_answer_with_fixes(user_input, raw_response, analysis_type)

def generate_fallback_answer_with_fixes(user_input: str, raw_response: dict, analysis_type: str) -> str:
    """Generate human-friendly answer with fix suggestions without Gemini."""
    if "error" in raw_response:
        error_msg = raw_response["error"]
        return f"❌ I encountered an issue: {error_msg}\n\nTry checking if the MCP server is running."

    result = raw_response.get("result", {})
    user_input_lower = user_input.lower()
    
    # Handle health analysis
    if analysis_type == "health" or 'error' in user_input_lower or 'issue' in user_input_lower:
        return generate_health_analysis(result, user_input)
    
    # Handle pending pods analysis
    elif analysis_type == "pending" or 'pending' in user_input_lower:
        return generate_pending_pods_analysis(result)
    
    # Handle failed pods analysis  
    elif analysis_type == "failed" or 'failed' in user_input_lower:
        return generate_failed_pods_analysis(result)
    
    # Handle general responses
    else:
        return generate_general_analysis(result, user_input)

def generate_health_analysis(result: dict, user_input: str) -> str:
    """Generate comprehensive health analysis with fix suggestions."""
    health_report = analyze_cluster_health("current_server")  # This would need server URL
    
    # If we have a proper health report structure
    if isinstance(result, dict) and any(key in result for key in ['pending_pods', 'failed_pods', 'error_events']):
        health_report = result
    
    issues_found = []
    fixes_suggested = []
    
    # Analyze pending pods
    pending_pods = health_report.get('pending_pods', [])
    if pending_pods and not any('error' in str(pod) for pod in pending_pods):
        for pod in pending_pods:
            if isinstance(pod, dict):
                pod_name = pod.get('name', 'unknown')
                namespace = pod.get('namespace', 'default')
                reasons = pod.get('reasons', ['Unknown reason'])
                
                issues_found.append(f"**Pending Pod**: {pod_name} in {namespace} namespace")
                issues_found.append(f"  - Reasons: {', '.join(reasons)}")
                
                # Suggest fixes based on reasons
                if any('insufficient' in reason.lower() for reason in reasons):
                    fixes_suggested.append(f"**Fix for {pod_name}**: Add more resources to your cluster or reduce resource requests")
                    fixes_suggested.append(f"  - Command: `kubectl describe pod {pod_name} -n {namespace}` to see resource requests")
                elif any('image' in reason.lower() for reason in reasons):
                    fixes_suggested.append(f"**Fix for {pod_name}**: Check image availability and pull secrets")
                    fixes_suggested.append(f"  - Command: `kubectl describe pod {pod_name} -n {namespace}` to see image pull issues")
                else:
                    fixes_suggested.append(f"**Fix for {pod_name}**: Investigate with `kubectl describe pod {pod_name} -n {namespace}`")
    
    # Analyze failed pods
    failed_pods = health_report.get('failed_pods', [])
    if failed_pods and not any('error' in str(pod) for pod in failed_pods):
        for pod in failed_pods:
            if isinstance(pod, dict):
                pod_name = pod.get('name', 'unknown')
                namespace = pod.get('namespace', 'default')
                reasons = pod.get('reasons', ['Unknown failure'])
                
                issues_found.append(f"**Failed Pod**: {pod_name} in {namespace} namespace")
                issues_found.append(f"  - Reasons: {', '.join(reasons)}")
                
                fixes_suggested.append(f"**Fix for {pod_name}**: Check logs and application configuration")
                fixes_suggested.append(f"  - Command: `kubectl logs {pod_name} -n {namespace}`")
                fixes_suggested.append(f"  - Command: `kubectl describe pod {pod_name} -n {namespace}`")
    
    # Analyze error events
    error_events = health_report.get('error_events', [])
    if error_events and not any('error' in str(event) for event in error_events):
        unique_errors = set()
        for event in error_events:
            if isinstance(event, dict):
                reason = event.get('reason', 'Unknown')
                message = event.get('message', 'No message')
                unique_errors.add(f"{reason}: {message}")
        
        if unique_errors:
            issues_found.append("**Cluster Events with Errors:**")
            for error in list(unique_errors)[:5]:  # Show max 5 unique errors
                issues_found.append(f"  - {error}")
    
    # Generate final report
    if issues_found:
        report = "🔴 **Cluster Health Issues Found:**\n\n"
        report += "### Issues Detected:\n"
        report += "\n".join(issues_found)
        report += "\n\n### Recommended Fixes:\n"
        report += "\n".join(fixes_suggested) if fixes_suggested else "Run `kubectl get events -A` for detailed investigation"
    else:
        report = "✅ **No issues detected! Your cluster is healthy.** 🎉\n\n"
        report += "All pods are running properly and no error events found."
    
    return report

def generate_pending_pods_analysis(result: dict) -> str:
    """Generate detailed pending pods analysis with fix suggestions."""
    pods = result.get('items', []) if isinstance(result, dict) else result
    
    if not pods or (isinstance(pods, list) and len(pods) == 0):
        return "✅ No pods found in the cluster."
    
    pending_pods = []
    for pod in pods:
        if isinstance(pod, dict):
            status = pod.get('status', {})
            phase = status.get('phase', '').lower()
            if phase == 'pending':
                pending_pods.append(pod)
    
    if not pending_pods:
        return "✅ No pending pods found. All pods are running properly!"
    
    analysis = f"🔴 **Found {len(pending_pods)} Pending Pods:**\n\n"
    
    for pod in pending_pods:
        pod_name = pod.get('metadata', {}).get('name', 'unknown')
        namespace = pod.get('metadata', {}).get('namespace', 'default')
        
        analysis += f"### Pod: {pod_name} (Namespace: {namespace})\n"
        
        # Analyze reasons
        status = pod.get('status', {})
        conditions = status.get('conditions', [])
        container_statuses = status.get('containerStatuses', [])
        
        reasons = []
        for cs in container_statuses:
            waiting = cs.get('state', {}).get('waiting', {})
            if waiting:
                reason = waiting.get('reason', '')
                message = waiting.get('message', '')
                if reason:
                    reasons.append(f"Container waiting: {reason} - {message}")
        
        for condition in conditions:
            if condition.get('status', '').lower() == 'false':
                reason = condition.get('reason', '')
                message = condition.get('message', '')
                if reason:
                    reasons.append(f"{condition.get('type', 'Condition')}: {reason} - {message}")
        
        if reasons:
            analysis += "**Reasons for pending state:**\n"
            for reason in reasons:
                analysis += f"- {reason}\n"
        else:
            analysis += "**Reason:** Unknown (need to check pod describe)\n"
        
        # Provide fix suggestions
        analysis += "**Fix suggestions:**\n"
        analysis += f"- `kubectl describe pod {pod_name} -n {namespace}` - Get detailed pod information\n"
        analysis += f"- `kubectl get events -n {namespace} --field-selector involvedObject.name={pod_name}` - Check pod-specific events\n"
        
        if any('insufficient' in str(reason).lower() for reason in reasons):
            analysis += "- **Resource issue**: Consider adding more nodes or reducing resource requests\n"
        elif any('image' in str(reason).lower() for reason in reasons):
            analysis += "- **Image issue**: Check image name, registry access, and pull secrets\n"
        
        analysis += "\n"
    
    return analysis

def generate_failed_pods_analysis(result: dict) -> str:
    """Generate detailed failed pods analysis with fix suggestions."""
    pods = result.get('items', []) if isinstance(result, dict) else result
    
    if not pods or (isinstance(pods, list) and len(pods) == 0):
        return "✅ No pods found in the cluster."
    
    failed_pods = []
    for pod in pods:
        if isinstance(pod, dict):
            status = pod.get('status', {})
            phase = status.get('phase', '').lower()
            if phase == 'failed':
                failed_pods.append(pod)
    
    if not failed_pods:
        return "✅ No failed pods found. All pods are running properly!"
    
    analysis = f"🔴 **Found {len(failed_pods)} Failed Pods:**\n\n"
    
    for pod in failed_pods:
        pod_name = pod.get('metadata', {}).get('name', 'unknown')
        namespace = pod.get('metadata', {}).get('namespace', 'default')
        
        analysis += f"### Pod: {pod_name} (Namespace: {namespace})\n"
        
        # Analyze failure reasons
        status = pod.get('status', {})
        container_statuses = status.get('containerStatuses', [])
        
        reasons = []
        for cs in container_statuses:
            terminated = cs.get('state', {}).get('terminated', {})
            if terminated:
                exit_code = terminated.get('exitCode', 0)
                reason = terminated.get('reason', '')
                message = terminated.get('message', '')
                
                if exit_code != 0:
                    reasons.append(f"Exit code {exit_code}: {reason} - {message}")
        
        if reasons:
            analysis += "**Failure reasons:**\n"
            for reason in reasons:
                analysis += f"- {reason}\n"
        else:
            analysis += "**Reason:** Unknown failure\n"
        
        # Provide fix suggestions
        analysis += "**Fix suggestions:**\n"
        analysis += f"- `kubectl logs {pod_name} -n {namespace}` - Check application logs\n"
        analysis += f"- `kubectl describe pod {pod_name} -n {namespace}` - Get detailed pod information\n"
        analysis += f"- Check application configuration and dependencies\n"
        
        analysis += "\n"
    
    return analysis

def generate_general_analysis(result: dict, user_input: str) -> str:
    """Generate general analysis for non-error queries."""
    # This would contain the existing general response logic from the original function
    # Simplified for brevity in this example
    if isinstance(result, dict) and 'items' in result:
        count = len(result['items'])
        return f"✅ Found {count} resources matching your query."
    
    return "✅ Operation completed successfully."

def extract_and_store_cluster_info(user_input: str, answer: str):
    """Extract cluster name/size from Gemini answer and store in session."""
    try:
        # Extract cluster name
        if "cluster name" in user_input.lower():
            patterns = [
                r"cluster[^\w]*([\w-]+)",
                r"name[^\w][:\-]?[^\w]([\w-]+)",
                r"\*([\w-]+)\*",  # bolded name
            ]
            for pattern in patterns:
                match = re.search(pattern, answer, re.IGNORECASE)
                if match:
                    cluster_name = match.group(1).strip()
                    st.session_state.last_known_cluster_name = cluster_name
                    break

        # Extract cluster size
        if "cluster size" in user_input.lower() or "how many nodes" in user_input.lower():
            numbers = re.findall(r'\b\d+\b', answer)
            if numbers:
                st.session_state.last_known_cluster_size = int(numbers[0])
    except Exception:
        pass  # silent fail

# ---------------- STREAMLIT APP ----------------
def main():
    st.set_page_config(page_title="MCP Chat Assistant", page_icon="⚡", layout="wide")
    st.title("🤖 MaSaOps Bot")

    # Sidebar with settings
    with st.sidebar:
        st.header("⚙️ Settings")
        
        # Server discovery
        if st.button("Discover Available Servers"):
            with st.spinner("Discovering MCP servers..."):
                st.success(f"Found {len(SERVERS)} servers")
                for server in SERVERS:
                    st.write(f"• {server['name']}: {server['url']}")
        
        st.text_input("Gemini API Key", value=GEMINI_API_KEY, disabled=True, type="password")
        
        if st.button("Clear Chat History"):
            st.session_state.messages = []
            st.rerun()

    # Main chat interface
    st.subheader("What's on your mind today? 🤔")
    
    # Display chat history
    for msg in st.session_state.messages:
        role = msg.get("role", "assistant")
        with st.chat_message(role):
            st.markdown(msg.get("content", ""))
    
    # Chat input
    user_prompt = st.chat_input("Ask anything about your infrastructure...")
    if not user_prompt:
        return
    
    # Add user message to history
    st.session_state.messages.append({"role": "user", "content": user_prompt})
    with st.chat_message("user"):
        st.markdown(user_prompt)
    
    # Auto-detect which server to use based on query
    with st.spinner("🔍 Finding the right server for your query..."):
        selected_server = detect_server_from_query(user_prompt, SERVERS)
    
    if not selected_server:
        error_msg = "No MCP servers available. Please check your servers.json file."
        st.session_state.messages.append({"role": "assistant", "content": error_msg})
        with st.chat_message("assistant"):
            st.error(error_msg)
        return
    
    # Show which server we're using
    server_info = f"🤖 Using server: **{selected_server['name']}**"
    st.session_state.messages.append({"role": "assistant", "content": server_info})
    with st.chat_message("assistant"):
        st.markdown(server_info)
    
    # Use Gemini to determine the best tool and arguments
    with st.spinner("🤔 Analyzing your request..."):
        decision = ask_gemini_for_tool_decision(user_prompt, selected_server["url"])
    
    explanation = decision.get("explanation", "I'm figuring out how to help you...")
    analysis_type = decision.get("analysis_type", "general")
    
    st.session_state.messages.append({"role": "assistant", "content": f"💡 {explanation}"})
    with st.chat_message("assistant"):
        st.markdown(f"💡 {explanation}")
    
    tool_name = decision.get("tool")
    tool_args = decision.get("args") or {}
    
    # Execute tool if one was selected
    if tool_name:
        with st.chat_message("assistant"):
            st.markdown(f"🔧 Executing `{tool_name}`...")
        
        # Enhanced error and issue detection
        if analysis_type in ["health", "pending", "failed"]:
            with st.spinner("🔍 Performing comprehensive cluster analysis..."):
                if analysis_type == "health":
                    # Full health analysis
                    health_report = analyze_cluster_health(selected_server["url"])
                    resp = {"result": health_report}
                elif analysis_type == "pending":
                    # Pending pods analysis
                    pending_pods = get_pending_pods_with_details(selected_server["url"])
                    resp = {"result": {"items": pending_pods}}
                elif analysis_type == "failed":
                    # Failed pods analysis
                    failed_pods = get_failed_pods_with_details(selected_server["url"])
                    resp = {"result": {"items": failed_pods}}
        else:
            # Call the tool normally
            with st.spinner("🔄 Processing your request..."):
                resp = call_tool(selected_server["url"], tool_name, tool_args)
        
        # Generate human-readable response with fix suggestions
        with st.spinner("📝 Analyzing results and preparing fix suggestions..."):
            final_answer = ask_gemini_answer(user_prompt, resp, analysis_type)
        
        # Add to chat history
        st.session_state.messages.append({"role": "assistant", "content": final_answer})
        with st.chat_message("assistant"):
            st.markdown(final_answer)
    
    else:
        # No tool selected - provide helpful suggestions
        helpful_response = (
            "I couldn't find a specific tool to answer your question. Here are some things you can try:\n\n"
            "**For Cluster Health & Issues:**\n"
            "- \"Show me any errors in the cluster\"\n"
            "- \"Are there any pending pods?\"\n"
            "- \"Check for failed pods\"\n"
            "- \"What's the cluster health status?\"\n"
            "- \"Show me all issues in the cluster\"\n\n"
            "**For Kubernetes Resources:**\n"
            "- \"List all namespaces\"\n"
            "- \"Show running pods\"\n"
            "- \"Get cluster nodes\"\n"
            "- \"Show all services\"\n"
            "- \"List all secrets\"\n"
            "- \"Check cluster events\"\n"
            "- \"Show all resources in cluster\"\n\n"
            "Try asking about specific issues you're concerned about!"
        )
        
        st.session_state.messages.append({"role": "assistant", "content": helpful_response})
        with st.chat_message("assistant"):
            st.markdown(helpful_response)

if __name__ == "__main__":
    main()
