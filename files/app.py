import os
import json
import time
import requests
import streamlit as st
from dotenv import load_dotenv
import google.generativeai as genai
from typing import Optional, Dict, Any, List
import re
import yaml
import subprocess

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
    st.session_state.available_tools_cache = {}  # Cache for tools per server
    st.session_state.helm_repos = {}  # Cache for Helm repositories

# ---------------- HELPER FUNCTIONS ----------------
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

def list_mcp_tools(server_url: str) -> List[Dict[str, Any]]:
    """Fetch available MCP tools for a specific server with caching."""
    if server_url in st.session_state.available_tools_cache:
        return st.session_state.available_tools_cache[server_url]
    
    resp = direct_mcp_call(server_url, "tools/list")
    tools = []
    
    if not isinstance(resp, dict):
        st.session_state.available_tools_cache[server_url] = tools
        return tools
    
    # Handle different response formats
    result = resp.get("result", {})
    if isinstance(result, dict):
        tools = result.get("tools", [])
    elif isinstance(result, list):
        tools = result
    elif "tools" in resp:
        tools = resp["tools"]
    
    # Cache the tools
    st.session_state.available_tools_cache[server_url] = tools
    return tools

def call_tool(server_url: str, name: str, arguments: dict):
    """Execute MCP tool by name with arguments."""
    if not name or not isinstance(arguments, dict):
        return {"error": "Invalid tool name or arguments"}
    
    return direct_mcp_call(server_url, "tools/call", {
        "name": name,
        "arguments": arguments
    })

def get_tool_descriptions(server_url: str) -> str:
    """Get detailed descriptions of all available tools."""
    tools = list_mcp_tools(server_url)
    descriptions = []
    
    for tool in tools:
        name = tool.get("name", "")
        description = tool.get("description", "No description available")
        input_schema = tool.get("inputSchema", {})
        
        # Extract parameter information
        params = input_schema.get("properties", {})
        param_desc = []
        for param_name, param_info in params.items():
            param_type = param_info.get("type", "unknown")
            param_desc.append(f"  - {param_name} ({param_type}): {param_info.get('description', 'No description')}")
        
        tool_desc = f"{name}: {description}"
        if param_desc:
            tool_desc += f"\nParameters:\n" + "\n".join(param_desc)
        descriptions.append(tool_desc)
    
    return "\n\n".join(descriptions)

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
            kubernetes_keywords = ["kubernetes", "k8s", "pod", "namespace", "deployment", 
                                 "service", "secret", "configmap", "node", "cluster", 
                                 "resource", "patch", "apply", "delete", "create", "get",
                                 "loadbalancer", "clusterip", "nodeport", "ingress",
                                 "helm", "chart", "deploy", "install"]
            if any(keyword in query_lower for keyword in kubernetes_keywords) and ("kubernetes" in server_name or "k8s" in server_name):
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

# ---------------- HELM FUNCTIONS (FLEXIBLE DEPLOYMENT) ----------------
def execute_helm_command(command: str) -> Dict[str, Any]:
    """Execute Helm command and return result."""
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=300)
        
        if result.returncode == 0:
            return {
                "success": True,
                "output": result.stdout,
                "message": f"Helm command executed successfully: {command}"
            }
        else:
            return {
                "success": False,
                "error": result.stderr,
                "output": result.stdout,
                "message": f"Helm command failed: {command}"
            }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": "Command timed out after 5 minutes",
            "message": f"Helm command timed out: {command}"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "message": f"Error executing Helm command: {command}"
        }

def deploy_helm_chart_flexible(chart_spec: str, release_name: str = None, namespace: str = "default", values: dict = None) -> Dict[str, Any]:
    """
    Flexible Helm chart deployment that can handle:
    - Official charts (gitlab, vault, karpenter, etc.)
    - Custom charts
    - Different repository formats
    """
    
    # Extract chart name and repository from the specification
    chart_spec_lower = chart_spec.lower()
    
    # Default values
    repo_url = None
    chart_name = chart_spec
    repo_name = f"repo-{int(time.time())}"  # Unique repo name
    
    # Map common official charts to their repositories
    official_charts = {
        "gitlab": "https://charts.gitlab.io/",
        "vault": "https://helm.releases.hashicorp.com",
        "karpenter": "oci://public.ecr.aws/karpenter",
        "nginx": "https://kubernetes.github.io/ingress-nginx",
        "cert-manager": "https://charts.jetstack.io",
        "prometheus": "https://prometheus-community.github.io/helm-charts",
        "grafana": "https://grafana.github.io/helm-charts",
        "elasticsearch": "https://helm.elastic.co",
        "redis": "https://charts.bitnami.com/bitnami",
        "postgresql": "https://charts.bitnami.com/bitnami",
        "mongodb": "https://charts.bitnami.com/bitnami",
        "mysql": "https://charts.bitnami.com/bitnami",
        "rabbitmq": "https://charts.bitnami.com/bitnami",
        "kafka": "https://charts.bitnami.com/bitnami",
        "jenkins": "https://charts.jenkins.io",
        "argo-cd": "https://argoproj.github.io/argo-helm",
        "istio": "https://istio-release.storage.googleapis.com/charts",
        "linkerd": "https://helm.linkerd.io/stable",
        "traefik": "https://helm.traefik.io/traefik"
    }
    
    # Check if it's an official chart we know about
    for known_chart, known_repo in official_charts.items():
        if known_chart in chart_spec_lower:
            repo_url = known_repo
            chart_name = known_chart
            repo_name = known_chart
            break
    
    # If no specific repo found, try to extract from the spec
    if "/" in chart_spec and "://" in chart_spec:
        # Chart spec contains repository URL
        parts = chart_spec.split("/")
        repo_url = "/".join(parts[:-1])
        chart_name = parts[-1]
    elif "/" in chart_spec:
        # Probably a repo/chart format
        parts = chart_spec.split("/")
        if len(parts) == 2:
            repo_name = parts[0]
            chart_name = parts[1]
            # Try to find repo URL from cache
            repo_url = st.session_state.helm_repos.get(repo_name)
    
    # Generate release name if not provided
    if not release_name:
        release_name = f"{chart_name}-{int(time.time())}"
    
    # Prepare values file if provided
    values_file = None
    if values:
        values_file = f"/tmp/values-{release_name}.yaml"
        with open(values_file, 'w') as f:
            yaml.dump(values, f)
    
    commands = []
    
    # Add repository if URL is known
    if repo_url:
        commands.append(f"helm repo add {repo_name} {repo_url}")
        commands.append("helm repo update")
    
    # Install chart
    install_cmd = f"helm install {release_name} {repo_name}/{chart_name} --namespace {namespace} --create-namespace"
    if values_file:
        install_cmd += f" -f {values_file}"
    
    commands.append(install_cmd)
    
    # Execute commands
    results = []
    for cmd in commands:
        result = execute_helm_command(cmd)
        results.append(result)
        if not result["success"]:
            # Stop if any command fails
            return result
    
    return {
        "success": True,
        "results": results,
        "message": f"Successfully deployed {chart_name} as {release_name} in namespace {namespace}",
        "release_name": release_name,
        "chart_name": chart_name
    }

def handle_any_deployment(user_query: str) -> Dict[str, Any]:
    """Handle deployment of any tool/chart mentioned in the query."""
    query_lower = user_query.lower()
    
    # Extract deployment target from query
    deployment_target = None
    release_name = None
    namespace = "default"
    
    # Common patterns for deployment queries
    patterns = [
        r"deploy\s+(?:the\s+)?(?:official\s+)?(\w+)(?:\s+helm\s+chart)?",
        r"install\s+(?:the\s+)?(?:official\s+)?(\w+)(?:\s+helm\s+chart)?",
        r"deploy\s+(\w+)\s+in\s+cluster",
        r"install\s+(\w+)\s+in\s+cluster",
        r"add\s+(\w+)\s+to\s+cluster",
        r"setup\s+(\w+)\s+in\s+cluster"
    ]
    
    for pattern in patterns:
        match = re.search(pattern, query_lower, re.IGNORECASE)
        if match:
            deployment_target = match.group(1)
            break
    
    # If no pattern matched, try to extract the first meaningful word after deploy/install
    if not deployment_target:
        words = user_query.split()
        for i, word in enumerate(words):
            if word.lower() in ["deploy", "install", "setup", "add"] and i + 1 < len(words):
                deployment_target = words[i + 1]
                break
    
    # Extract namespace if specified
    namespace_match = re.search(r"namespace\s+(\w+)", query_lower)
    if namespace_match:
        namespace = namespace_match.group(1)
    
    # Extract release name if specified
    release_match = re.search(r"(?:as|named)\s+(\w+)", query_lower)
    if release_match:
        release_name = release_match.group(1)
    
    if deployment_target:
        return {
            "type": "helm_deploy",
            "target": deployment_target,
            "release_name": release_name,
            "namespace": namespace,
            "explanation": f"Deploying {deployment_target} using Helm"
        }
    
    return {
        "type": "unknown",
        "explanation": "Could not determine what to deploy from the query"
    }

# ---------------- GEMINI FUNCTIONS ----------------
def ask_gemini_for_tool_decision(query: str, server_url: str):
    """Use Gemini to map user query -> MCP tool + arguments or deployment action."""
    tools = list_mcp_tools(server_url)
    tool_names = [t["name"] for t in tools if "name" in t]
    tool_descriptions = get_tool_descriptions(server_url)

    # First, check if this is a deployment request
    deployment_result = handle_any_deployment(query)
    if deployment_result["type"] == "helm_deploy":
        return deployment_result

    # Inject context from session state if available
    context_notes = ""
    if st.session_state.last_known_cluster_name:
        context_notes += f"\nUser previously interacted with cluster: {st.session_state.last_known_cluster_name}"
    if st.session_state.last_known_cluster_size:
        context_notes += f"\nLast known cluster size: {st.session_state.last_known_cluster_size} nodes"

    instruction = f"""
You are an AI agent that maps user queries to MCP tools for Kubernetes operations or deployment actions.
User query: "{query}"
{context_notes}

Available tools and their descriptions:
{tool_descriptions}

Rules:
- If the user wants to deploy/install any tool, chart, or application, respond with deployment action.
- For viewing resources, use kubectl_get.
- For modifying resources, use kubectl_patch or kubectl_edit.
- For creating resources, use kubectl_apply or kubectl_create.
- For deleting resources, use kubectl_delete.
- Extract resource names, types, and namespaces from the query.

For deployment requests, respond with:
{{"type": "helm_deploy", "target": "chart-name", "release_name": "optional-name", "namespace": "optional-namespace", "explanation": "Short explanation"}}

For tool requests, respond with:
{{"type": "mcp_tool", "tool": "tool_name", "args": {{arguments}}, "explanation": "Short explanation"}}

If unsure, respond with:
{{"type": "unknown", "explanation": "Need more information"}}
"""
    
    if not GEMINI_AVAILABLE:
        # Fallback logic
        query_lower = query.lower()
        
        # Deployment detection fallback
        if any(keyword in query_lower for keyword in ["deploy", "install", "setup", "add"]):
            deployment_result = handle_any_deployment(query)
            if deployment_result["type"] == "helm_deploy":
                return deployment_result
        
        # View operations
        if "all resources" in query_lower or "everything" in query_lower or "all" in query_lower:
            return {
                "type": "mcp_tool",
                "tool": "kubectl_get",
                "args": {"resourceType": "all", "allNamespaces": True},
                "explanation": "User wants to see all resources in cluster"
            }
        
        return {"type": "unknown", "explanation": "Need more information about what you want to do"}
    
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(instruction)
        text = response.text.strip()
        
        # Try to extract JSON from response
        parsed = None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            # Try to extract JSON from text
            try:
                start = text.find('{')
                end = text.rfind('}') + 1
                if start != -1 and end != -1 and end > start:
                    json_str = text[start:end]
                    parsed = json.loads(json_str)
            except:
                pass
        
        if not parsed:
            parsed = {"type": "unknown", "explanation": f"Invalid response: {text}"}
        
        return parsed
        
    except Exception as e:
        return {"type": "unknown", "explanation": f"Gemini error: {str(e)}"}

def ask_gemini_answer(user_input: str, raw_response: dict) -> str:
    """Use Gemini to convert raw response into human-friendly answer."""
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
            "- Respond in clear, natural, conversational English.\n"
            "- If it's a deployment result, explain what was deployed and any next steps.\n"
            "- If it's a list, format with bullet points.\n"
            "- If error occurred, DO NOT show raw error. Politely explain what went wrong and suggest what user can do.\n"
            "- NEVER show JSON, code, or internal errors to user unless asked.\n"
            "- Be helpful, friendly, and precise."
        )
        
        resp = model.generate_content(prompt)
        answer = getattr(resp, "text", str(resp)).strip()

        return answer

    except Exception as e:
        return generate_fallback_answer(user_input, raw_response)

def generate_fallback_answer(user_input: str, raw_response: dict) -> str:
    """Generate human-friendly answer without Gemini."""
    
    if "success" in raw_response:
        if raw_response["success"]:
            if "release_name" in raw_response:
                return f"✅ Successfully deployed {raw_response.get('chart_name', 'the application')} as '{raw_response['release_name']}'. The deployment is now running in your cluster."
            return "✅ Operation completed successfully."
        else:
            error_msg = raw_response.get("error", "Unknown error occurred")
            return f"❌ Deployment failed: {error_msg}\n\nPlease check the chart name and repository URL, and ensure you have proper cluster access."
    
    if "error" in raw_response:
        error_msg = raw_response["error"]
        return f"❌ Operation failed: {error_msg}"
    
    result = raw_response.get("result", {})
    
    if isinstance(result, dict) and "items" in result:
        count = len(result["items"])
        return f"Found {count} resources matching your query."
    
    return "Operation completed successfully."

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
        
        # Show available tools for selected server
        if st.button("Refresh Available Tools"):
            st.session_state.available_tools_cache = {}
            st.rerun()
        
        if st.button("Clear Chat History"):
            st.session_state.messages = []
            st.session_state.available_tools_cache = {}
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
    
    # Use Gemini to determine the best action
    with st.spinner("🤔 Analyzing your request..."):
        decision = ask_gemini_for_tool_decision(user_prompt, selected_server["url"])
    
    explanation = decision.get("explanation", "I'm figuring out how to help you...")
    st.session_state.messages.append({"role": "assistant", "content": f"💡 {explanation}"})
    with st.chat_message("assistant"):
        st.markdown(f"💡 {explanation}")
    
    action_type = decision.get("type", "unknown")
    
    # Handle different action types
    if action_type == "helm_deploy":
        # Deploy any Helm chart
        target = decision.get("target")
        release_name = decision.get("release_name")
        namespace = decision.get("namespace", "default")
        
        if target:
            with st.chat_message("assistant"):
                st.markdown(f"🚀 Deploying **{target}** in namespace **{namespace}**...")
            
            with st.spinner(f"🔄 Deploying {target} (this may take a few minutes)..."):
                result = deploy_helm_chart_flexible(target, release_name, namespace)
            
            # Generate human-readable response
            final_answer = ask_gemini_answer(user_prompt, result)
            
            # Add to chat history
            st.session_state.messages.append({"role": "assistant", "content": final_answer})
            with st.chat_message("assistant"):
                st.markdown(final_answer)
        else:
            error_msg = "I couldn't determine what you want to deploy. Please be more specific (e.g., 'deploy gitlab', 'install nginx', 'setup prometheus')."
            st.session_state.messages.append({"role": "assistant", "content": error_msg})
            with st.chat_message("assistant"):
                st.error(error_msg)
    
    elif action_type == "mcp_tool":
        # Use MCP tool
        tool_name = decision.get("tool")
        tool_args = decision.get("args") or {}
        
        if tool_name:
            with st.chat_message("assistant"):
                st.markdown(f"🔧 Executing `{tool_name}`...")
            
            with st.spinner("🔄 Processing your request..."):
                resp = call_tool(selected_server["url"], tool_name, tool_args)
            
            # Generate human-readable response
            final_answer = ask_gemini_answer(user_prompt, resp)
            
            # Add to chat history
            st.session_state.messages.append({"role": "assistant", "content": final_answer})
            with st.chat_message("assistant"):
                st.markdown(final_answer)
        else:
            error_msg = "I couldn't determine which tool to use for your request."
            st.session_state.messages.append({"role": "assistant", "content": error_msg})
            with st.chat_message("assistant"):
                st.error(error_msg)
    
    else:
        # Unknown action - provide helpful guidance
        helpful_response = """
**I can help you with various Kubernetes operations:**

**🚀 Deployment Commands:**
- "Deploy GitLab in my cluster"
- "Install Vault Helm chart"  
- "Setup Prometheus monitoring"
- "Add nginx ingress controller"
- "Install cert-manager for TLS certificates"
- "Deploy Redis for caching"
- "Setup Jenkins CI/CD"

**🔧 Kubernetes Operations:**
- "Show all pods in all namespaces"
- "Get cluster nodes and status"
- "List all services"
- "Check cluster resources"
- "Create a new deployment from YAML"

**📊 Monitoring & Management:**
- "Show cluster health"
- "Check resource usage"
- "List all running applications"

**💡 Examples:**
- `deploy gitlab` - Installs GitLab using official Helm chart
- `install nginx` - Sets up nginx ingress controller  
- `setup prometheus` - Deploys monitoring stack
- `show all pods` - Lists all pods in cluster

What would you like to do today?
"""
        
        st.session_state.messages.append({"role": "assistant", "content": helpful_response})
        with st.chat_message("assistant"):
            st.markdown(helpful_response)

if __name__ == "__main__":
    main()
