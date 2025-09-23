import os
import json
import time
import requests
import streamlit as st
from dotenv import load_dotenv
import google.generativeai as genai
from typing import Optional, Dict, Any, List
import re
import logging

# ---------------- CONFIG & LOGGING ----------------
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyC7iRO4NnyQz144aEc6RiVUNzjL9C051V8")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configure Gemini if available
GEMINI_AVAILABLE = False
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        GEMINI_AVAILABLE = True
        logger.info("Gemini AI configured successfully")
    except Exception as e:
        logger.error(f"Gemini configuration failed: {e}")
        GEMINI_AVAILABLE = False

# Load servers list from servers.json
def load_servers() -> list:
    try:
        with open("servers.json") as f:
            data = json.load(f)
        servers = data.get("servers", [])
        logger.info(f"Loaded {len(servers)} servers from configuration")
        return servers
    except Exception as e:
        logger.error(f"Failed to load servers: {e}")
        return []

SERVERS = load_servers()

# Initialize session state
def initialize_session_state():
    if "messages" not in st.session_state:
        st.session_state.messages = []
        st.session_state.last_known_cluster_name = None
        st.session_state.last_known_cluster_size = None
        st.session_state.last_known_namespace = "default"
        st.session_state.available_servers = SERVERS
        st.session_state.conversation_context = {
            "recent_queries": [],
            "detected_resources": set(),
            "cluster_health_status": None,
            "common_namespaces": ["default", "kube-system"]
        }
        st.session_state.error_patterns = {
            "pending_pods": [],
            "image_pull_errors": [],
            "resource_issues": []
        }

# ---------------- IMPROVED HELPERS ----------------
def direct_mcp_call(server_url: str, method: str, params: Optional[Dict[str, Any]] = None, timeout: int = 30) -> Dict[str, Any]:
    """Enhanced direct call to MCP server with better error handling and logging"""
    payload = {
        "jsonrpc": "2.0",
        "id": int(time.time() * 1000),
        "method": method,
        "params": params or {}
    }
    
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream, */*"
    }
    
    try:
        logger.info(f"MCP Call: {method} to {server_url}")
        start_time = time.time()
        
        response = requests.post(server_url, json=payload, headers=headers, timeout=timeout)
        response.raise_for_status()
        
        elapsed_time = time.time() - start_time
        logger.info(f"MCP Response received in {elapsed_time:.2f}s")
        
        text = response.text.strip()
        
        # Handle SSE-style responses
        if text.startswith("data:") or "data:" in text:
            lines = text.split('\n')
            for line in lines:
                if line.startswith('data:'):
                    data_content = line[5:].strip()
                    try:
                        result = json.loads(data_content)
                        logger.debug(f"SSE response parsed successfully")
                        return result
                    except json.JSONDecodeError:
                        logger.warning("SSE response contained non-JSON data")
                        return {"result": data_content}
        
        # Handle regular JSON
        try:
            result = response.json()
            logger.debug("JSON response parsed successfully")
            return result
        except json.JSONDecodeError:
            logger.warning("Response was not valid JSON, returning as text")
            return {"result": text}
            
    except requests.exceptions.Timeout:
        error_msg = f"MCP server timeout after {timeout}s"
        logger.error(error_msg)
        return {"error": error_msg}
    except requests.exceptions.RequestException as e:
        error_msg = f"MCP server request failed: {str(e)}"
        logger.error(error_msg)
        return {"error": error_msg}
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        logger.error(error_msg)
        return {"error": error_msg}

def list_mcp_tools(server_url: str) -> List[Dict[str, Any]]:
    """Fetch available MCP tools with caching and error handling"""
    cache_key = f"tools_{server_url}"
    
    # Check cache first
    if hasattr(st.session_state, 'tool_cache'):
        cached = st.session_state.tool_cache.get(cache_key)
        if cached and time.time() - cached.get('timestamp', 0) < 300:  # 5 minute cache
            return cached.get('tools', [])
    
    resp = direct_mcp_call(server_url, "tools/list")
    tools = []
    
    if not isinstance(resp, dict):
        logger.warning(f"Unexpected tools response format: {type(resp)}")
        return tools
    
    result = resp.get("result", {})
    
    # Handle different response formats
    if isinstance(result, dict):
        tools = result.get("tools", [])
    elif isinstance(result, list):
        tools = result
    elif "tools" in resp:
        tools = resp["tools"]
    
    # Cache the result
    if not hasattr(st.session_state, 'tool_cache'):
        st.session_state.tool_cache = {}
    st.session_state.tool_cache[cache_key] = {
        'tools': tools,
        'timestamp': time.time()
    }
    
    logger.info(f"Found {len(tools)} tools for server {server_url}")
    return tools

def call_tool(server_url: str, name: str, arguments: dict) -> Dict[str, Any]:
    """Enhanced tool execution with validation and logging"""
    if not name or not isinstance(arguments, dict):
        error_msg = "Invalid tool name or arguments"
        logger.error(error_msg)
        return {"error": error_msg}
    
    logger.info(f"Calling tool {name} with args: {arguments}")
    return direct_mcp_call(server_url, "tools/call", {
        "name": name,
        "arguments": arguments
    })

def enhanced_sanitize_args(args: dict, user_query: str = "") -> dict:
    """Intelligent argument sanitization with context awareness"""
    if not args:
        args = {}

    fixed = args.copy()
    query_lower = user_query.lower()
    
    # Update conversation context
    if user_query:
        st.session_state.conversation_context["recent_queries"].append(user_query)
        # Keep only last 10 queries
        st.session_state.conversation_context["recent_queries"] = \
            st.session_state.conversation_context["recent_queries"][-10:]
    
    # Enhanced namespace detection
    if "namespace" not in fixed and user_query:
        namespace_patterns = [
            r"in\s+ns\s+['\"]?(\S+)['\"]?",
            r"in\s+namespace\s+['\"]?(\S+)['\"]?",
            r"-n\s+['\"]?(\S+)['\"]?",
            r"namespace\s+['\"]?(\S+)['\"]?",
            r"from\s+ns\s+['\"]?(\S+)['\"]?",
            r"in\s+the\s+(\S+)\s+namespace",
        ]
        
        for pattern in namespace_patterns:
            match = re.search(pattern, query_lower)
            if match:
                detected_ns = match.group(1)
                # Remove trailing punctuation
                detected_ns = re.sub(r'[.,!?;:]$', '', detected_ns)
                fixed["namespace"] = detected_ns
                st.session_state.last_known_namespace = detected_ns
                logger.info(f"Detected namespace from query: {detected_ns}")
                break
    
    # Use last known namespace if none detected
    if "namespace" not in fixed and st.session_state.last_known_namespace:
        fixed["namespace"] = st.session_state.last_known_namespace
    
    # Enhanced resource type detection and mapping
    resource_mappings = {
        "ns": "namespaces", "namespace": "namespaces", "namespaces": "namespaces",
        "pod": "pods", "pods": "pods", "po": "pods",
        "node": "nodes", "nodes": "nodes", "no": "nodes",
        "deploy": "deployments", "deployment": "deployments", "deployments": "deployments",
        "svc": "services", "service": "services", "services": "services",
        "cm": "configmaps", "configmap": "configmaps", "configmaps": "configmaps",
        "secret": "secrets", "secrets": "secrets",
        "event": "events", "events": "events", "ev": "events",
        "all": "all", "everything": "all",
        "ingress": "ingresses", "ingresses": "ingresses", "ing": "ingresses",
        "pvc": "persistentvolumeclaims", "persistentvolumeclaim": "persistentvolumeclaims",
        "pv": "persistentvolumes", "persistentvolume": "persistentvolumes",
        "sa": "serviceaccounts", "serviceaccount": "serviceaccounts",
    }
    
    current_resource = fixed.get("resourceType") or fixed.get("resource")
    if current_resource and current_resource.lower() in resource_mappings:
        fixed["resourceType"] = resource_mappings[current_resource.lower()]
        logger.info(f"Mapped resource type: {current_resource} -> {fixed['resourceType']}")
    
    # Intelligent "all namespaces" detection
    all_ns_patterns = [
        r"all\s+namespaces", r"all\s+ns", r"across\s+all\s+namespaces",
        r"every\s+namespace", r"ella\s+pod", r"all\s+pod",
        r"in\s+all\s+namespaces", r"from\s+all\s+namespaces"
    ]
    
    if any(re.search(pattern, query_lower) for pattern in all_ns_patterns):
        fixed["allNamespaces"] = True
        fixed.pop("namespace", None)
        logger.info("Enabled allNamespaces based on query pattern")
    
    # Handle cluster-wide requests
    if "cluster" in query_lower and "wide" in query_lower:
        fixed["allNamespaces"] = True
    
    # Handle describe commands specifically
    if "describe" in query_lower:
        # Extract resource name for describe commands
        describe_patterns = [
            r"describe\s+(\S+)\s+(\S+)",
            r"describe\s+pod\s+['\"]?(\S+)['\"]?",
            r"show\s+details\s+of\s+pod\s+['\"]?(\S+)['\"]?",
        ]
        
        for pattern in describe_patterns:
            match = re.search(pattern, query_lower)
            if match:
                if not fixed.get("name"):
                    fixed["name"] = match.group(1) if len(match.groups()) == 1 else match.group(2)
                break
    
    # Set intelligent defaults
    if fixed.get("resourceType") == "pods" and "namespace" not in fixed and "allNamespaces" not in fixed:
        fixed["namespace"] = st.session_state.last_known_namespace or "default"
    
    # Handle output format requests
    if "wide" in query_lower or "detailed" in query_lower:
        fixed["output"] = "wide"
    elif "json" in query_lower or "raw" in query_lower:
        fixed["output"] = "json"
    elif "yaml" in query_lower:
        fixed["output"] = "yaml"
    
    logger.debug(f"Sanitized args: {fixed}")
    return fixed

def intelligent_server_selection(query: str, available_servers: list) -> Optional[Dict[str, Any]]:
    """Enhanced server selection with context awareness"""
    query_lower = query.lower()
    
    # Check conversation context first
    recent_queries = st.session_state.conversation_context.get("recent_queries", [])
    if recent_queries:
        # If recent queries were about Kubernetes, prefer Kubernetes server
        k8s_keywords = ["pod", "namespace", "deployment", "service", "kube", "cluster"]
        if any(any(keyword in q.lower() for keyword in k8s_keywords) for q in recent_queries[-3:]):
            for server in available_servers:
                if "kube" in server["name"].lower() or "kubernetes" in server["name"].lower():
                    logger.info(f"Selected server based on context: {server['name']}")
                    return server
    
    # Keyword-based server selection with scoring
    server_scores = {}
    
    for server in available_servers:
        score = 0
        server_name_lower = server["name"].lower()
        server_url_lower = server["url"].lower()
        
        # Kubernetes-related queries
        k8s_keywords = ["pod", "namespace", "deployment", "service", "node", "cluster", 
                       "kubectl", "k8s", "kubernetes", "secret", "configmap", "event"]
        if any(keyword in query_lower for keyword in k8s_keywords):
            if any(kw in server_name_lower for kw in ["kube", "kubernetes", "k8s"]):
                score += 10
            elif "jenkins" in server_name_lower or "argo" in server_name_lower:
                score -= 5
        
        # Jenkins-related queries
        jenkins_keywords = ["jenkins", "job", "build", "pipeline", "ci/cd"]
        if any(keyword in query_lower for keyword in jenkins_keywords):
            if "jenkins" in server_name_lower:
                score += 10
        
        # ArgoCD-related queries
        argocd_keywords = ["argocd", "gitops", "application", "sync", "deploy"]
        if any(keyword in query_lower for keyword in argocd_keywords):
            if "argo" in server_name_lower or "gitops" in server_name_lower:
                score += 10
        
        # Exact matches in tool names
        try:
            tools = list_mcp_tools(server["url"])
            tool_names = [t.get("name", "").lower() for t in tools]
            
            for tool_name in tool_names:
                if tool_name in query_lower:
                    score += 15
                    break
                
                # Partial matches
                for word in query_lower.split():
                    if word in tool_name and len(word) > 3:
                        score += 5
                        break
        except Exception:
            pass
        
        server_scores[server["name"]] = score
    
    # Select server with highest score
    if server_scores:
        best_server_name = max(server_scores, key=server_scores.get)
        if server_scores[best_server_name] > 0:
            for server in available_servers:
                if server["name"] == best_server_name:
                    logger.info(f"Selected server '{server['name']}' with score {server_scores[best_server_name]}")
                    return server
    
    # Fallback to first available server
    if available_servers:
        logger.info(f"Using fallback server: {available_servers[0]['name']}")
        return available_servers[0]
    
    return None

# ---------------- ENHANCED RESOURCE FUNCTIONS ----------------
def get_intelligent_cluster_overview(server_url: str) -> Dict[str, Any]:
    """Get comprehensive cluster overview with health assessment"""
    overview = {
        "cluster_health": "unknown",
        "resources": {},
        "issues": [],
        "recommendations": []
    }
    
    try:
        # Get basic cluster info
        nodes_response = call_tool(server_url, "kubectl_get", {"resourceType": "nodes"})
        pods_response = call_tool(server_url, "kubectl_get", {
            "resourceType": "pods", 
            "allNamespaces": True
        })
        
        # Analyze nodes
        if nodes_response and not nodes_response.get("error"):
            nodes = nodes_response.get("result", {}).get("items", [])
            ready_nodes = 0
            total_nodes = len(nodes)
            
            for node in nodes:
                if isinstance(node, dict):
                    conditions = node.get("status", {}).get("conditions", [])
                    for condition in conditions:
                        if (condition.get("type") == "Ready" and 
                            condition.get("status") == "True"):
                            ready_nodes += 1
                            break
            
            overview["resources"]["nodes"] = {
                "total": total_nodes,
                "ready": ready_nodes,
                "not_ready": total_nodes - ready_nodes
            }
            
            if ready_nodes == total_nodes and total_nodes > 0:
                overview["cluster_health"] = "healthy"
            elif ready_nodes > 0:
                overview["cluster_health"] = "degraded"
            else:
                overview["cluster_health"] = "unhealthy"
        
        # Analyze pods
        if pods_response and not pods_response.get("error"):
            pods = pods_response.get("result", {}).get("items", [])
            pod_statuses = {}
            
            for pod in pods:
                if isinstance(pod, dict):
                    status = pod.get("status", {}).get("phase", "Unknown")
                    pod_statuses[status] = pod_statuses.get(status, 0) + 1
                    
                    # Detect issues
                    if status == "Pending":
                        overview["issues"].append({
                            "type": "pending_pod",
                            "message": f"Pod {pod.get('metadata', {}).get('name')} is pending",
                            "namespace": pod.get('metadata', {}).get('namespace', 'default')
                        })
                    elif status == "Failed":
                        overview["issues"].append({
                            "type": "failed_pod", 
                            "message": f"Pod {pod.get('metadata', {}).get('name')} failed",
                            "namespace": pod.get('metadata', {}).get('namespace', 'default')
                        })
            
            overview["resources"]["pods"] = pod_statuses
        
        # Generate recommendations
        if overview["cluster_health"] == "unhealthy":
            overview["recommendations"].append("Check node status and network connectivity")
        if overview["resources"].get("pods", {}).get("Pending", 0) > 0:
            overview["recommendations"].append("Investigate pending pods with 'show pending pods'")
        
        st.session_state.conversation_context["cluster_health_status"] = overview["cluster_health"]
        
    except Exception as e:
        logger.error(f"Error getting cluster overview: {e}")
        overview["error"] = str(e)
    
    return overview

def enhanced_pending_pods_analysis(server_url: str) -> List[Dict[str, Any]]:
    """Enhanced pending pods analysis with intelligent reasoning"""
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

            status = pod.get("status", {}).get("phase", "")
            if status.lower() == "pending":
                metadata = pod.get("metadata", {})
                name = metadata.get("name", "unknown")
                namespace = metadata.get("namespace", "default")

                # Store namespace context
                st.session_state.last_known_namespace = namespace

                # Enhanced analysis
                pod_spec = pod.get("spec", {})
                status_details = pod.get("status", {})
                
                # Analyze conditions for more detailed reasoning
                conditions = status_details.get("conditions", [])
                events_response = call_tool(server_url, "kubectl_describe", {
                    "resourceType": "pod",
                    "name": name,
                    "namespace": namespace
                })

                reason = "Unknown reason"
                detailed_analysis = ""
                suggested_fix = ""

                # Analyze common pending reasons
                if events_response and not events_response.get("error"):
                    describe_text = events_response.get("result", "")
                    if isinstance(describe_text, str):
                        # Enhanced pattern matching for common issues
                        if "Insufficient cpu" in describe_text:
                            reason = "Insufficient CPU resources"
                            detailed_analysis = "The cluster doesn't have enough CPU to schedule this pod"
                            suggested_fix = "Scale up cluster nodes or reduce CPU requests in pod spec"
                        elif "Insufficient memory" in describe_text:
                            reason = "Insufficient memory resources"
                            detailed_analysis = "The cluster doesn't have enough memory to schedule this pod"
                            suggested_fix = "Add more memory or reduce memory requests in pod spec"
                        elif "node(s) had taint" in describe_text:
                            reason = "Node taints preventing scheduling"
                            detailed_analysis = "No nodes available that match pod's tolerations"
                            suggested_fix = "Add tolerations to pod spec or remove taints from nodes"
                        elif "Failed to pull image" in describe_text:
                            reason = "Image pull failure"
                            detailed_analysis = "Cannot pull the container image from registry"
                            suggested_fix = "Check image name, tag, and registry accessibility"
                        elif "node(s) didn't match Pod's node affinity" in describe_text:
                            reason = "Node affinity rules not satisfied"
                            detailed_analysis = "No nodes match the pod's node affinity requirements"
                            suggested_fix = "Check node labels and pod's node affinity rules"
                        elif "persistentvolumeclaim" in describe_text.lower():
                            reason = "PVC binding issues"
                            detailed_analysis = "Persistent Volume Claim is not bound"
                            suggested_fix = "Check PVC status and storage class availability"
                        else:
                            # Extract events section for manual analysis
                            events_start = describe_text.find("Events:")
                            if events_start != -1:
                                events_text = describe_text[events_start:]
                                lines = events_text.split('\n')
                                for line in lines[1:6]:  # First few event lines
                                    if line.strip() and "Warning" in line:
                                        reason = f"Scheduling issue: {line.strip()}"
                                        break

                pending_pods.append({
                    "name": name,
                    "namespace": namespace,
                    "reason": reason,
                    "detailed_analysis": detailed_analysis,
                    "suggested_fix": suggested_fix,
                    "pod_spec": {
                        "containers": len(pod_spec.get("containers", [])),
                        "node_selector": pod_spec.get("nodeSelector"),
                        "tolerations": pod_spec.get("tolerations"),
                        "resource_requests": pod_spec.get("containers", [{}])[0].get("resources", {})
                    }
                })

        return pending_pods

    except Exception as e:
        logger.error(f"Error in pending pods analysis: {e}")
        return [{"error": f"Analysis failed: {str(e)}"}]

# ---------------- ENHANCED GEMINI FUNCTIONS ----------------
def intelligent_tool_selection(query: str, server_url: str) -> Dict[str, Any]:
    """Enhanced tool selection with better context understanding"""
    tools = list_mcp_tools(server_url)
    tool_names = [t["name"] for t in tools if "name" in t]

    # Build comprehensive context
    context = {
        "conversation_history": st.session_state.conversation_context.get("recent_queries", [])[-5:],
        "last_namespace": st.session_state.last_known_namespace,
        "cluster_health": st.session_state.conversation_context.get("cluster_health_status"),
        "available_tools": tool_names,
        "common_patterns": st.session_state.error_patterns
    }

    # Enhanced instruction prompt
    instruction = f"""
You are an intelligent Kubernetes/Jenkins/ArgoCD assistant. Analyze the user query and select the appropriate tool.

USER QUERY: "{query}"

CONTEXT:
- Recent queries: {context['conversation_history']}
- Last namespace used: {context['last_namespace']}
- Cluster health: {context['cluster_health']}
- Available tools: {', '.join(tool_names)}

INTELLIGENT MAPPING RULES:
1. CLUSTER HEALTH & DIAGNOSTICS:
   - "cluster status", "health check", "how's my cluster" → get_intelligent_cluster_overview
   - "errors", "issues", "problems", "what's wrong" → kubectl_get events + intelligent analysis
   - "pending pods", "stuck pods" → enhanced_pending_pods_analysis

2. RESOURCE QUERIES:
   - "all pods", "show pods", "list pods" → kubectl_get pods with allNamespaces if "all" mentioned
   - "pods in [namespace]" → kubectl_get pods with specific namespace
   - "describe pod X" → kubectl_describe pod with automatic namespace detection
   - "get nodes", "cluster nodes" → kubectl_get nodes

3. INTELLIGENT DEFAULTS:
   - If no namespace specified, use context from recent queries
   - For "describe" commands, always try to extract resource name
   - For "get" commands, infer resource type from query

4. COMMAND PATTERNS:
   - "kubectl get pods -n X" → kubectl_get with namespace X
   - "kubectl describe pod X" → kubectl_describe with pod X
   - "show me running services" → kubectl_get services

RESPONSE FORMAT (JSON only):
{{
    "tool": "tool_name" | null,
    "args": {{"arg1": "value1", ...}} | null,
    "explanation": "Brief reasoning for tool selection",
    "confidence": 0.0-1.0,
    "alternative_suggestions": ["suggestion1", "suggestion2"]
}}
"""

    # Pre-process query for common patterns
    query_lower = query.lower().strip()
    
    # High-confidence direct mappings
    direct_mappings = {
        "all pods": {"tool": "kubectl_get", "args": {"resourceType": "pods", "allNamespaces": True}},
        "show pods": {"tool": "kubectl_get", "args": {"resourceType": "pods", "allNamespaces": True}},
        "list pods": {"tool": "kubectl_get", "args": {"resourceType": "pods", "allNamespaces": True}},
        "cluster status": {"tool": "get_intelligent_cluster_overview", "args": {}},
        "pending pods": {"tool": "enhanced_pending_pods_analysis", "args": {}},
        "get nodes": {"tool": "kubectl_get", "args": {"resourceType": "nodes"}},
        "cluster nodes": {"tool": "kubectl_get", "args": {"resourceType": "nodes"}},
    }
    
    for pattern, mapping in direct_mappings.items():
        if pattern in query_lower:
            mapping["args"] = enhanced_sanitize_args(mapping["args"], query)
            return {
                "tool": mapping["tool"],
                "args": mapping["args"],
                "explanation": f"Direct mapping for '{pattern}'",
                "confidence": 0.95,
                "alternative_suggestions": []
            }

    if not GEMINI_AVAILABLE:
        # Enhanced fallback logic
        return enhanced_fallback_selection(query, server_url)

    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(instruction)
        text = response.text.strip()
        
        # Parse response
        parsed = parse_gemini_response(text)
        if not parsed:
            return enhanced_fallback_selection(query, server_url)
        
        # Sanitize arguments
        parsed["args"] = enhanced_sanitize_args(parsed.get("args") or {}, query)
        
        return parsed
        
    except Exception as e:
        logger.error(f"Gemini tool selection error: {e}")
        return enhanced_fallback_selection(query, server_url)

def parse_gemini_response(text: str) -> Optional[Dict[str, Any]]:
    """Parse Gemini response with multiple fallback strategies"""
    # Strategy 1: Direct JSON parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    
    # Strategy 2: Extract JSON from text
    json_match = re.search(r'\{[^{}]*\}', text)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass
    
    # Strategy 3: Look for code blocks
    code_match = re.search(r'```(?:json)?\s*(\{.*\})\s*```', text, re.DOTALL)
    if code_match:
        try:
            return json.loads(code_match.group(1))
        except json.JSONDecodeError:
            pass
    
    logger.warning(f"Could not parse Gemini response: {text[:100]}...")
    return None

def enhanced_fallback_selection(query: str, server_url: str) -> Dict[str, Any]:
    """Enhanced fallback when Gemini is unavailable"""
    query_lower = query.lower()
    
    # Simple pattern matching with confidence scoring
    patterns = [
        (r"describe pod (\S+)", "kubectl_describe", {"resourceType": "pod", "name": None}, 0.9),
        (r"pods in namespace (\S+)", "kubectl_get", {"resourceType": "pods", "namespace": None}, 0.8),
        (r"show me (\S+) in (\S+)", "kubectl_get", {"resourceType": None, "namespace": None}, 0.7),
        (r"get (\S+)", "kubectl_get", {"resourceType": None}, 0.6),
        (r"list (\S+)", "kubectl_get", {"resourceType": None}, 0.6),
    ]
    
    for pattern, tool, args, confidence in patterns:
        match = re.search(pattern, query_lower)
        if match:
            groups = match.groups()
            if tool == "kubectl_describe" and len(groups) >= 1:
                args["name"] = groups[0]
            elif tool == "kubectl_get" and "namespace" in args and len(groups) >= 2:
                args["resourceType"] = groups[0]
                args["namespace"] = groups[1]
            elif tool == "kubectl_get" and "resourceType" in args and len(groups) >= 1:
                args["resourceType"] = groups[0]
            
            args = enhanced_sanitize_args(args, query)
            return {
                "tool": tool,
                "args": args,
                "explanation": f"Pattern match: {pattern}",
                "confidence": confidence,
                "alternative_suggestions": []
            }
    
    # Default fallback
    return {
        "tool": None,
        "args": None,
        "explanation": "Could not determine appropriate tool",
        "confidence": 0.0,
        "alternative_suggestions": ["Try being more specific about what you want to see"]
    }

def generate_intelligent_response(user_input: str, raw_response: dict, tool_used: str = None) -> str:
    """Generate intelligent, context-aware responses"""
    if not GEMINI_AVAILABLE:
        return generate_enhanced_fallback_response(user_input, raw_response, tool_used)

    try:
        # Build comprehensive context
        context = {
            "user_query": user_input,
            "tool_used": tool_used,
            "conversation_history": st.session_state.conversation_context.get("recent_queries", [])[-3:],
            "last_namespace": st.session_state.last_known_namespace,
            "cluster_health": st.session_state.conversation_context.get("cluster_health_status"),
            "raw_data": raw_response
        }

        prompt = f"""
You are an expert Kubernetes/Jenkins/ArgoCD administrator. Provide a helpful, accurate response.

USER QUESTION: {user_input}
TOOL USED: {tool_used}
CONTEXT: {json.dumps(context, indent=2)}

RESPONSE GUIDELINES:
1. BE PRECISE & TECHNICAL: Provide exact information from the data
2. BE HELPFUL: Suggest next steps or explanations for issues
3. BE CONCISE: Avoid unnecessary information
4. USE EMOJIS: Appropriately for status (✅, ⚠️, ❌, 🔍, etc.)
5. FORMAT WELL: Use bullet points, code blocks, and clear sections
6. PROVIDE CONTEXT: Relate to previous queries when relevant

SPECIAL CASES:
- For errors: Explain what went wrong and suggest fixes
- For empty results: Suggest what to check next
- For cluster issues: Provide remediation steps
- For describe commands: Highlight key events and status

Respond in clear, professional English:
"""

        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(prompt)
        answer = getattr(response, "text", str(response)).strip()

        # Extract and store contextual information
        update_conversation_context(user_input, answer, raw_response)

        return answer

    except Exception as e:
        logger.error(f"Error generating intelligent response: {e}")
        return generate_enhanced_fallback_response(user_input, raw_response, tool_used)

def generate_enhanced_fallback_response(user_input: str, raw_response: dict, tool_used: str = None) -> str:
    """Enhanced fallback response generation"""
    user_input_lower = user_input.lower()
    
    # Handle errors
    if "error" in raw_response:
        error_msg = raw_response["error"]
        if "not found" in error_msg.lower():
            return "❌ Resource not found. Please verify the name and namespace. You can list available resources with `kubectl get <resource-type> -n <namespace>`."
        elif "timeout" in error_msg.lower():
            return "⏰ Request timeout. The cluster might be under heavy load or there might be network issues."
        return f"❌ Error: {error_msg}"

    result = raw_response.get("result", {})
    
    # Handle different tool responses intelligently
    if tool_used == "kubectl_get":
        return format_kubectl_get_response(result, user_input)
    elif tool_used == "kubectl_describe":
        return format_describe_response(result, user_input)
    elif tool_used == "enhanced_pending_pods_analysis":
        return format_pending_pods_response(result)
    elif tool_used == "get_intelligent_cluster_overview":
        return format_cluster_overview_response(result)
    
    # Default formatting
    if isinstance(result, dict) and "items" in result:
        return format_resource_list(result["items"], user_input)
    
    return f"✅ Operation completed:\n```json\n{json.dumps(result, indent=2)}\n```"

def format_kubectl_get_response(result: Any, user_input: str) -> str:
    """Format kubectl get responses intelligently"""
    if isinstance(result, dict) and "items" in result:
        items = result["items"]
        if not items:
            return "📭 No resources found."
        
        resource_type = "resources"
        if "pod" in user_input:
            resource_type = "pods"
            return format_pod_list(items)
        elif "node" in user_input:
            resource_type = "nodes"
            return format_node_list(items)
        elif "service" in user_input or "svc" in user_input:
            resource_type = "services"
        elif "namespace" in user_input or "ns" in user_input:
            resource_type = "namespaces"
            return format_namespace_list(items)
        
        return f"📋 Found {len(items)} {resource_type}."
    
    return f"✅ Response:\n```\n{str(result)[:500]}\n```"

def format_pod_list(pods: List[Dict]) -> str:
    """Format pod list with intelligent grouping"""
    if not pods:
        return "📭 No pods found."
    
    grouped = {}
    for pod in pods:
        if isinstance(pod, dict):
            namespace = pod.get("metadata", {}).get("namespace", "default")
            status = pod.get("status", {}).get("phase", "Unknown")
            
            if namespace not in grouped:
                grouped[namespace] = {}
            if status not in grouped[namespace]:
                grouped[namespace][status] = []
            
            grouped[namespace][status].append(pod.get("metadata", {}).get("name", "unknown"))
    
    lines = [f"📊 Found {len(pods)} pods across {len(grouped)} namespaces:"]
    
    for namespace, statuses in grouped.items():
        lines.append(f"\n**Namespace: {namespace}**")
        for status, pod_names in statuses.items():
            status_emoji = "✅" if status == "Running" else "⚠️" if status == "Pending" else "❌"
            lines.append(f"  {status_emoji} {status}: {len(pod_names)} pods")
            if len(pod_names) <= 5:  # Show names for small lists
                for name in pod_names:
                    lines.append(f"    • {name}")
    
    return "\n".join(lines)

def update_conversation_context(user_input: str, response: str, raw_data: dict):
    """Update conversation context with intelligent information extraction"""
    try:
        # Extract namespace mentions
        ns_matches = re.findall(r"namespace[:\s]*['\"]?(\S+)['\"]?", response, re.IGNORECASE)
        if ns_matches:
            st.session_state.last_known_namespace = ns_matches[0]
        
        # Extract cluster information
        if "cluster" in user_input.lower():
            name_match = re.search(r"cluster[:\s]*['\"]?(\S+)['\"]?", response, re.IGNORECASE)
            if name_match:
                st.session_state.last_known_cluster_name = name_match.group(1)
            
            size_match = re.search(r"(\d+)\s+node", response, re.IGNORECASE)
            if size_match:
                st.session_state.last_known_cluster_size = int(size_match.group(1))
        
        # Update detected resources
        resource_types = ["pod", "service", "deployment", "node", "namespace"]
        for resource in resource_types:
            if resource in user_input.lower():
                st.session_state.conversation_context["detected_resources"].add(resource)
        
        # Keep sets manageable
        if len(st.session_state.conversation_context["detected_resources"]) > 10:
            st.session_state.conversation_context["detected_resources"] = set(
                list(st.session_state.conversation_context["detected_resources"])[-10:]
            )
            
    except Exception as e:
        logger.warning(f"Context update error: {e}")

# ---------------- STREAMLIT APP ----------------
def main():
    st.set_page_config(
        page_title="🤖 MaSaOps Bot - Intelligent Kubernetes Assistant", 
        page_icon="⚡", 
        layout="wide"
    )
    
    # Initialize session state
    initialize_session_state()
    
    st.title("🤖 MaSaOps Bot - Intelligent Kubernetes Assistant")
    st.markdown("### Your AI-powered Kubernetes, Jenkins, and ArgoCD expert")

    # Sidebar with enhanced information
    with st.sidebar:
        st.header("⚙️ Intelligent Settings")
        
        # Server status
        st.subheader("🔌 Connected Servers")
        for server in st.session_state.available_servers:
            status = "✅" if server.get("enabled", True) else "❌"
            st.write(f"{status} **{server['name']}**")
        
        # Cluster context
        st.subheader("📊 Cluster Context")
        if st.session_state.last_known_cluster_name:
            st.write(f"**Cluster:** {st.session_state.last_known_cluster_name}")
        if st.session_state.last_known_cluster_size:
            st.write(f"**Nodes:** {st.session_state.last_known_cluster_size}")
        if st.session_state.last_known_namespace:
            st.write(f"**Namespace:** {st.session_state.last_known_namespace}")
        
        # Quick actions
        st.subheader("🚀 Quick Actions")
        if st.button("🔄 Refresh Cluster Info"):
            with st.spinner("Getting cluster overview..."):
                if st.session_state.available_servers:
                    server = intelligent_server_selection("cluster status", st.session_state.available_servers)
                    if server:
                        overview = get_intelligent_cluster_overview(server["url"])
                        st.success(f"Cluster health: {overview.get('cluster_health', 'unknown')}")
        
        if st.button("🗑️ Clear Chat History"):
            st.session_state.messages = []
            st.rerun()
        
        # Diagnostics
        st.subheader("🔍 Diagnostics")
        st.write(f"🤖 Gemini AI: {'✅ Available' if GEMINI_AVAILABLE else '❌ Unavailable'}")
        st.write(f"💬 Messages: {len(st.session_state.messages)}")

    # Main chat interface
    st.subheader("💬 Chat with MaSaOps Bot")
    st.markdown("Ask about pods, nodes, deployments, cluster health, or any Kubernetes resources!")

    # Display chat history
    for msg in st.session_state.messages:
        role = msg.get("role", "assistant")
        with st.chat_message(role):
            st.markdown(msg.get("content", ""))
            if msg.get("metadata"):
                with st.expander("📊 Response Details"):
                    st.json(msg.get("metadata"))

    # Chat input
    user_prompt = st.chat_input("Ask anything about your infrastructure...")
    if not user_prompt:
        return

    # Add user message to history
    st.session_state.messages.append({"role": "user", "content": user_prompt})
    with st.chat_message("user"):
        st.markdown(user_prompt)

    # Process the query
    try:
        # Step 1: Intelligent server selection
        with st.spinner("🔍 Finding the best server for your query..."):
            selected_server = intelligent_server_selection(user_prompt, st.session_state.available_servers)

        if not selected_server:
            error_msg = "❌ No suitable servers available. Please check your servers configuration."
            st.session_state.messages.append({"role": "assistant", "content": error_msg})
            with st.chat_message("assistant"):
                st.error(error_msg)
            return

        # Show server info
        server_info = f"🤖 **Using server:** {selected_server['name']}"
        st.session_state.messages.append({"role": "assistant", "content": server_info})
        with st.chat_message("assistant"):
            st.markdown(server_info)

        # Step 2: Intelligent tool selection
        with st.spinner("🤔 Analyzing your request with AI..."):
            decision = intelligent_tool_selection(user_prompt, selected_server["url"])

        # Show decision explanation
        explanation = decision.get("explanation", "Analyzing your request...")
        confidence = decision.get("confidence", 0)
        explanation_msg = f"💡 {explanation} (Confidence: {confidence:.0%})"
        
        st.session_state.messages.append({"role": "assistant", "content": explanation_msg})
        with st.chat_message("assistant"):
            st.markdown(explanation_msg)

        # Step 3: Execute tool if available
        tool_name = decision.get("tool")
        tool_args = decision.get("args") or {}

        if tool_name:
            with st.chat_message("assistant"):
                st.markdown(f"🔧 Executing `{tool_name}`...")

            # Execute the appropriate tool
            if tool_name == "enhanced_pending_pods_analysis":
                with st.spinner("🔍 Performing intelligent pending pods analysis..."):
                    result = enhanced_pending_pods_analysis(selected_server["url"])
                    resp = {"result": result}
            elif tool_name == "get_intelligent_cluster_overview":
                with st.spinner("🔄 Gathering comprehensive cluster overview..."):
                    result = get_intelligent_cluster_overview(selected_server["url"])
                    resp = {"result": result}
            else:
                with st.spinner(f"🔄 Executing {tool_name}..."):
                    resp = call_tool(selected_server["url"], tool_name, tool_args)

            # Step 4: Generate intelligent response
            with st.spinner("📝 Crafting intelligent response..."):
                final_answer = generate_intelligent_response(user_prompt, resp, tool_name)

            # Store and display response
            st.session_state.messages.append({
                "role": "assistant", 
                "content": final_answer,
                "metadata": {"tool_used": tool_name, "args": tool_args}
            })
            with st.chat_message("assistant"):
                st.markdown(final_answer)

        else:
            # No tool selected - provide helpful guidance
            helpful_response = generate_helpful_guidance(user_prompt)
            
            st.session_state.messages.append({"role": "assistant", "content": helpful_response})
            with st.chat_message("assistant"):
                st.markdown(helpful_response)

    except Exception as e:
        error_msg = f"❌ Sorry, I encountered an unexpected error: {str(e)}"
        logger.error(f"Main processing error: {e}")
        st.session_state.messages.append({"role": "assistant", "content": error_msg})
        with st.chat_message("assistant"):
            st.error(error_msg)

def generate_helpful_guidance(query: str) -> str:
    """Generate helpful guidance when no specific tool is selected"""
    query_lower = query.lower()
    
    guidance = "🤔 I want to help you! Here are some things you can ask me:\n\n"
    
    categories = {
        "🔍 **Cluster Diagnostics**:": [
            "Show cluster health status",
            "Check for pending pods",
            "Show cluster events with errors",
            "Get node status",
            "Check resource usage"
        ],
        "📊 **Resource Information**:": [
            "Show all pods [in namespace X]",
            "List services/deployments/configmaps",
            "Describe pod [name]",
            "Show pods in efk namespace",
            "Get all resources in cluster"
        ],
        "⚡ **Quick Commands**:": [
            "kubectl get pods -A",
            "kubectl describe pod my-pod",
            "kubectl get nodes",
            "kubectl get events -A"
        ],
        "🔧 **Troubleshooting**:": [
            "Why are pods pending?",
            "Show me any errors",
            "What's wrong with my cluster?",
            "Check image pull issues"
        ]
    }
    
    for category, examples in categories.items():
        guidance += f"{category}\n"
        for example in examples:
            guidance += f"  • {example}\n"
        guidance += "\n"
    
    guidance += "💡 **Pro Tip**: Be specific! Instead of 'show pods', try 'show running pods in default namespace'"
    
    return guidance

# Add missing formatting functions
def format_node_list(nodes):
    """Format node list response"""
    if not nodes:
        return "📭 No nodes found."
    
    ready_nodes = 0
    node_info = []
    
    for node in nodes:
        if isinstance(node, dict):
            name = node.get("metadata", {}).get("name", "unknown")
            conditions = node.get("status", {}).get("conditions", [])
            is_ready = any(c.get("type") == "Ready" and c.get("status") == "True" for c in conditions)
            
            if is_ready:
                ready_nodes += 1
                status = "✅ Ready"
            else:
                status = "❌ Not Ready"
            
            node_info.append(f"  • {name} - {status}")
    
    return f"🖥️ **Nodes Overview** ({ready_nodes}/{len(nodes)} ready):\n" + "\n".join(node_info)

def format_namespace_list(namespaces):
    """Format namespace list response"""
    if not namespaces:
        return "📭 No namespaces found."
    
    namespace_names = []
    for ns in namespaces:
        if isinstance(ns, dict):
            name = ns.get("metadata", {}).get("name", "unknown")
            status = ns.get("status", {}).get("phase", "Active")
            namespace_names.append(f"  • {name} - {status}")
    
    return f"📁 **Namespaces** ({len(namespaces)} total):\n" + "\n".join(namespace_names)

def format_pending_pods_response(pods):
    """Format pending pods response"""
    if not pods:
        return "✅ No pending pods found. All pods are running normally!"
    
    if isinstance(pods, list) and pods and "error" in pods[0]:
        return f"❌ Error analyzing pending pods: {pods[0]['error']}"
    
    response = [f"⚠️ **Pending Pods Analysis** ({len(pods)} found):\n"]
    
    for pod in pods:
        if isinstance(pod, dict) and "name" in pod:
            response.append(f"\n🔸 **Pod:** {pod['name']}")
            response.append(f"   **Namespace:** {pod.get('namespace', 'default')}")
            response.append(f"   **Reason:** {pod.get('reason', 'Unknown')}")
            
            if pod.get("detailed_analysis"):
                response.append(f"   **Analysis:** {pod['detailed_analysis']}")
            if pod.get("suggested_fix"):
                response.append(f"   **Suggested Fix:** {pod['suggested_fix']}")
    
    return "\n".join(response)

def format_cluster_overview_response(overview):
    """Format cluster overview response"""
    if not overview or "error" in overview:
        return "❌ Could not retrieve cluster overview."
    
    response = ["🏥 **Cluster Health Overview**\n"]
    
    # Health status
    health = overview.get("cluster_health", "unknown")
    health_emoji = "✅" if health == "healthy" else "⚠️" if health == "degraded" else "❌"
    response.append(f"{health_emoji} **Status:** {health.title()}\n")
    
    # Resources summary
    resources = overview.get("resources", {})
    if "nodes" in resources:
        nodes = resources["nodes"]
        response.append(f"🖥️ **Nodes:** {nodes.get('ready', 0)}/{nodes.get('total', 0)} ready")
    
    if "pods" in resources:
        pods = resources["pods"]
        total_pods = sum(pods.values())
        response.append(f"📦 **Pods:** {total_pods} total")
        for status, count in pods.items():
            emoji = "✅" if status == "Running" else "⚠️" if status == "Pending" else "❌"
            response.append(f"  {emoji} {status}: {count}")
    
    # Issues
    issues = overview.get("issues", [])
    if issues:
        response.append(f"\n⚠️ **Issues Found:** {len(issues)}")
        for issue in issues[:3]:  # Show first 3 issues
            response.append(f"  • {issue.get('message', 'Unknown issue')}")
    
    # Recommendations
    recommendations = overview.get("recommendations", [])
    if recommendations:
        response.append(f"\n💡 **Recommendations:**")
        for rec in recommendations:
            response.append(f"  • {rec}")
    
    return "\n".join(response)

def format_describe_response(result, user_input):
    """Format describe command responses"""
    if isinstance(result, str):
        if "not found" in result.lower():
            return "❌ Resource not found. Please check the name and namespace."
        
        # Extract key information for better presentation
        lines = result.split('\n')
        key_sections = []
        current_section = []
        
        for line in lines:
            if line.strip() and not line.startswith(' ') and ':' in line:
                if current_section:
                    key_sections.append('\n'.join(current_section))
                    current_section = []
            current_section.append(line)
        
        if current_section:
            key_sections.append('\n'.join(current_section))
        
        response = ["🔍 **Detailed Description**\n"]
        response.append("```")
        response.append(result[:2000])  # Limit output size
        response.append("```")
        
        return '\n'.join(response)
    
    return f"```\n{str(result)}\n```"

if __name__ == "__main__":
    main()
