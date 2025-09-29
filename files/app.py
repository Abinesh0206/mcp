# ---------------- CONFIG ----------------
import os
import json
import time
import requests
import streamlit as st
from dotenv import load_dotenv
import google.generativeai as genai
from typing import Optional, Dict, Any
import re
import yaml

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
    
    # FIX 1: Handle "all namespaces" request for pods and other resources
    if (fixed.get("resourceType") in ["pods", "services", "deployments", "secrets", "configmaps"] and 
        "namespace" not in fixed):
        # Auto-set allNamespaces for resources that support it
        fixed["allNamespaces"] = True
    
    # Handle explicit "all" namespace request
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
    
    # ✅ CRITICAL FIX: DO NOT convert namespace creation to YAML.
    # Most MCP servers expect: { "resourceType": "namespaces", "name": "abi" }
    # So we LEAVE the 'name' field as-is and DO NOT create a manifest.
    # Remove the old YAML conversion logic entirely.
    
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

def get_all_cluster_resources(server_url: str):
    """Get all resources in the cluster by querying multiple resource types."""
    resource_types = [
        "pods", "services", "deployments", "configmaps", 
        "secrets", "namespaces", "nodes"
    ]
    
    all_resources = {}
    
    for resource_type in resource_types:
        try:
            # FIX 2: Always use allNamespaces for resources that support it
            params = {"resourceType": resource_type}
            if resource_type not in ["namespaces", "nodes"]:  # These don't need namespaces
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

IMPORTANT RULES FOR KUBERNETES:
- When user asks for "all pods", "show all pods", or similar, ALWAYS set allNamespaces=true
- When user wants to see resources across all namespaces, use allNamespaces=true
- For pods, services, deployments, secrets, configmaps - default to all namespaces unless specified
- For nodes and namespaces, don't use namespace parameters
- For creating namespaces: use kubectl_create with resourceType: "namespaces" and name: "<namespace_name>"
- For deleting namespaces: use kubectl_delete with resourceType: "namespaces" and name: "<namespace_name>"

Rules:
- Only choose from the tools above.
- If the query clearly maps to a tool, return tool + args in JSON.
- If the user asks for "all resources" or "everything in cluster", use kubectl_get with appropriate arguments.
- If unsure, set tool=null and args=null.

Respond ONLY in strict JSON:
{{"tool": "<tool_name>" | null, "args": {{}} | null, "explanation": "Short explanation"}}
"""
    if not GEMINI_AVAILABLE:
        # Enhanced fallback logic
        query_lower = query.lower()
        
        # Handle namespace creation
        if any(word in query_lower for word in ["create namespace", "create ns", "make namespace"]):
            # Extract namespace name from query
            namespace_match = re.search(r'(?:namespace|ns)[\s]+([\w-]+)', query_lower)
            if namespace_match:
                namespace_name = namespace_match.group(1)
                return {
                    "tool": "kubectl_create",
                    "args": {"resourceType": "namespaces", "name": namespace_name},
                    "explanation": f"Creating namespace '{namespace_name}'"
                }
            else:
                # Try to extract any word after "create namespace"
                words = query_lower.split()
                try:
                    idx = words.index("namespace") if "namespace" in words else words.index("ns")
                    if idx + 1 < len(words):
                        namespace_name = words[idx + 1]
                        if namespace_name.isalnum() or '-' in namespace_name:
                            return {
                                "tool": "kubectl_create",
                                "args": {"resourceType": "namespaces", "name": namespace_name},
                                "explanation": f"Creating namespace '{namespace_name}'"
                            }
                except:
                    pass
        
        # Handle namespace deletion
        if any(word in query_lower for word in ["delete namespace", "delete ns", "remove namespace"]):
            namespace_match = re.search(r'(?:namespace|ns)[\s]+([\w-]+)', query_lower)
            if namespace_match:
                namespace_name = namespace_match.group(1)
                return {
                    "tool": "kubectl_delete",
                    "args": {"resourceType": "namespaces", "name": namespace_name},
                    "explanation": f"Deleting namespace '{namespace_name}'"
                }
        
        # Handle "all pods in all namespaces" requests
        if any(phrase in query_lower for phrase in ["all pods", "all pod", "all namespace", "all namespaces", "ella pod", "ella namespace"]):
            return {
                "tool": "kubectl_get",
                "args": {"resourceType": "pods", "allNamespaces": True},
                "explanation": "User wants to see all pods across all namespaces"
            }
        elif "all resources" in query_lower or "everything" in query_lower or "ella resource" in query_lower:
            return {
                "tool": "kubectl_get",
                "args": {"resourceType": "all", "allNamespaces": True},
                "explanation": "User wants to see all resources in cluster across all namespaces"
            }
        elif "pods" in query_lower or "pod" in query_lower:
            return {
                "tool": "kubectl_get",
                "args": {"resourceType": "pods", "allNamespaces": True},
                "explanation": "User wants to see all pods across all namespaces"
            }
        elif "services" in query_lower or "svc" in query_lower:
            return {
                "tool": "kubectl_get",
                "args": {"resourceType": "services", "allNamespaces": True},
                "explanation": "User wants to see all services across all namespaces"
            }
        elif "secrets" in query_lower:
            return {
                "tool": "kubectl_get",
                "args": {"resourceType": "secrets", "allNamespaces": True},
                "explanation": "User wants to see all secrets across all namespaces"
            }
        elif "nodes" in query_lower:
            return {
                "tool": "kubectl_get",
                "args": {"resourceType": "nodes"},
                "explanation": "User wants to see all nodes"
            }
        else:
            return {"tool": None, "args": None, "explanation": "Gemini not configured; fallback to chat reply."}
    
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
            parsed = {"tool": None, "args": None, "explanation": f"Gemini invalid response: {text}"}
        
        parsed["args"] = sanitize_args(parsed.get("args") or {})
        return parsed
        
    except Exception as e:
        return {"tool": None, "args": None, "explanation": f"Gemini error: {str(e)}"}

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
            "- Respond in clear, natural, conversational English.\n"
            "- If it's a list, format with bullet points.\n"
            "- If it's status, explain health and issues clearly.\n"
            "- If error occurred, DO NOT show raw error. Politely explain what went wrong and suggest what user can do.\n"
            "- If cluster name or size was inferred, mention that explicitly.\n"
            "- If cluster size = 1, say: 'This appears to be a minimal/single-node cluster.'\n"
            "- NEVER show JSON, code, or internal errors to user unless asked.\n"
            "- Be helpful, friendly, and precise."
        )
        
        resp = model.generate_content(prompt)
        answer = getattr(resp, "text", str(resp)).strip()

        # Extract and store cluster info for future context
        extract_and_store_cluster_info(user_input, answer)

        return answer

    except Exception as e:
        return generate_fallback_answer(user_input, raw_response)

def generate_fallback_answer(user_input: str, raw_response: dict) -> str:
    """Generate human-friendly answer without Gemini."""
    if "error" in raw_response:
        error_msg = raw_response["error"]
        
        # Handle namespace creation errors specifically
        if "create" in user_input.lower() and "namespace" in user_input.lower():
            if "already exists" in error_msg.lower():
                return "This namespace already exists in the cluster."
            elif "forbidden" in error_msg.lower() or "permission" in error_msg.lower():
                return "I don't have permission to create namespaces. Please check your Kubernetes RBAC permissions."
            else:
                return f"Sorry, I couldn't create the namespace: {error_msg}"
        
        if "cluster" in user_input.lower():
            return "I couldn't retrieve the cluster information right now. Please check if the MCP server is running and accessible."
        return f"Sorry, I encountered an issue: {error_msg}"
    
    result = raw_response.get("result", {})
    
    # Handle namespace creation success
    if "create" in user_input.lower() and "namespace" in user_input.lower():
        if result and not result.get("error"):
            # Extract namespace name from user input
            namespace_match = re.search(r'(?:namespace|ns)[\s]+([\w-]+)', user_input.lower())
            namespace_name = namespace_match.group(1) if namespace_match else "the requested"
            return f"✅ Successfully created namespace '{namespace_name}'!"
    
    # Handle namespace deletion success
    if "delete" in user_input.lower() and "namespace" in user_input.lower():
        if result and not result.get("error"):
            namespace_match = re.search(r'(?:namespace|ns)[\s]+([\w-]+)', user_input.lower())
            namespace_name = namespace_match.group(1) if namespace_match else "the requested"
            return f"✅ Successfully deleted namespace '{namespace_name}'!"
    
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
                # Show namespace information for pods
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
            
            if "secret" in user_input.lower():
                secrets = [f"{item.get('metadata', {}).get('name', 'unnamed')} in {item.get('metadata', {}).get('namespace', 'default')} namespace" for item in items]
                if secrets:
                    return f"Found {count} secrets across all namespaces:\n" + "\n".join([f"• {secret}" for secret in secrets])
                else:
                    return "No secrets found in any namespace."
        
        # Handle all-resources response
        if any(key in result for key in ["pods", "services", "deployments", "configmaps", "secrets", "namespaces", "nodes"]):
            summary = "**Cluster Resources Summary:**\n\n"
            total_count = 0
            
            for resource_type, resources in result.items():
                if isinstance(resources, list):
                    count = len(resources)
                    total_count += count
                    summary += f"• {resource_type.capitalize()}: {count}\n"
                elif "items" in str(resources):
                    # Handle nested items structure
                    try:
                        if isinstance(resources, dict) and "items" in resources:
                            count = len(resources["items"])
                            total_count += count
                            summary += f"• {resource_type.capitalize()}: {count}\n"
                    except:
                        summary += f"• {resource_type.capitalize()}: Data available\n"
            
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
    st.session_state.messages.append({"role": "assistant", "content": f"💡 {explanation}"})
    with st.chat_message("assistant"):
        st.markdown(f"💡 {explanation}")
    
    tool_name = decision.get("tool")
    tool_args = decision.get("args") or {}
    
    # Execute tool if one was selected
    if tool_name:
        with st.chat_message("assistant"):
            st.markdown(f"🔧 Executing `{tool_name}`...")
        
        # Special handling for "all resources" request
        if (user_prompt.lower().strip() in ["show me all resources in cluster", "get all resources", "all resources"] or
            ("all" in user_prompt.lower() and "resource" in user_prompt.lower()) or
            "ella resource" in user_prompt.lower()):
            with st.spinner("🔄 Gathering all cluster resources (this may take a moment)..."):
                all_resources = get_all_cluster_resources(selected_server["url"])
                resp = {"result": all_resources}
        else:
            # Call the tool normally
            with st.spinner("🔄 Processing your request..."):
                resp = call_tool(selected_server["url"], tool_name, tool_args)
        
        # Generate human-readable response
        with st.spinner("📝 Formatting response..."):
            final_answer = ask_gemini_answer(user_prompt, resp)
        
        # Add to chat history
        st.session_state.messages.append({"role": "assistant", "content": final_answer})
        with st.chat_message("assistant"):
            st.markdown(final_answer)
    
    else:
        # No tool selected - provide helpful suggestions
        helpful_response = (
            "I couldn't find a specific tool to answer your question. Here are some things you can try:\n\n"
            "**For Kubernetes:**\n"
            "- \"Create namespace abinesh\"\n"
            "- \"Delete namespace undefined\"\n"
            "- \"List all pods in all namespaces\"\n"
            "- \"Show all services across all namespaces\"\n"
            "- \"Get cluster nodes\"\n"
            "- \"List all secrets in all namespaces\"\n"
            "- \"Show all resources in cluster\"\n\n"
            "**For Jenkins:**\n"
            "- \"List all jobs\"\n"
            "- \"Show build status\"\n\n"
            "**For ArgoCD:**\n"
            "- \"List applications\"\n"
            "- \"Show application status\"\n\n"
            "Or try being more specific about what you'd like to see!"
        )
        
        st.session_state.messages.append({"role": "assistant", "content": helpful_response})
        with st.chat_message("assistant"):
            st.markdown(helpful_response)

if __name__ == "__main__":
    main()

