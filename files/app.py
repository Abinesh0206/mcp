# ---------------- CONFIG ----------------
import os
import json
import time
import requests
import streamlit as st
from dotenv import load_dotenv
import google.generativeai as genai
from typing import Optional, Dict, Any, List
import re

# ---------------- CONFIG ----------------
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyApANXlk_-Pc0MrveXl6Umq0KLxdk5wr8c")
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
    st.session_state.server_tools_cache = {}  # Cache for server tools

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

def list_mcp_tools(server_url: str) -> List[Dict[str, Any]]:
    """Fetch available MCP tools for a specific server with caching."""
    # Check cache first
    if server_url in st.session_state.server_tools_cache:
        return st.session_state.server_tools_cache[server_url]
    
    resp = direct_mcp_call(server_url, "tools/list")
    if not isinstance(resp, dict):
        tools = []
    else:
        # Handle different response formats
        result = resp.get("result", {})
        if isinstance(result, dict):
            tools = result.get("tools", [])
        elif isinstance(result, list):
            tools = result
        elif "tools" in resp:
            tools = resp["tools"]
        else:
            tools = []
    
    # Cache the tools
    st.session_state.server_tools_cache[server_url] = tools
    return tools

def get_tool_descriptions(server_url: str) -> str:
    """Get detailed tool descriptions for AI decision making."""
    tools = list_mcp_tools(server_url)
    if not tools:
        return "No tools available"
    
    descriptions = []
    for tool in tools:
        name = tool.get("name", "unknown")
        description = tool.get("description", "No description available")
        input_schema = tool.get("inputSchema", {})
        properties = input_schema.get("properties", {})
        required = input_schema.get("required", [])
        
        param_info = []
        for param_name, param_details in properties.items():
            param_type = param_details.get("type", "string")
            param_desc = param_details.get("description", "No description")
            is_required = " (REQUIRED)" if param_name in required else " (optional)"
            param_info.append(f"  - {param_name} ({param_type}){is_required}: {param_desc}")
        
        params_str = "\n".join(param_info) if param_info else "  - No parameters"
        descriptions.append(f"• {name}:\n  Description: {description}\n  Parameters:\n{params_str}")
    
    return "\n\n".join(descriptions)

def call_tool(server_url: str, name: str, arguments: dict):
    """Execute MCP tool by name with arguments."""
    if not name or not isinstance(arguments, dict):
        return {"error": "Invalid tool name or arguments"}
    
    return direct_mcp_call(server_url, "tools/call", {
        "name": name,
        "arguments": arguments
    })

def sanitize_args(args: dict, tool_schema: dict = None) -> dict:
    """Fix arguments before sending to MCP tools based on tool schema."""
    if not args:
        return {}

    fixed = args.copy()
    
    # Get required parameters from schema if available
    required_params = []
    if tool_schema:
        input_schema = tool_schema.get("inputSchema", {})
        required_params = input_schema.get("required", [])
        properties = input_schema.get("properties", {})
    
    # Handle kubectl_create specific logic
    if tool_schema and tool_schema.get("name") == "kubectl_create":
        # For kubectl_create, resourceType is typically required
        if "resourceType" not in fixed and "resource" in fixed:
            fixed["resourceType"] = fixed.pop("resource")
        
        # For namespace creation, use SINGULAR form
        if fixed.get("resourceType") in ["namespace", "namespaces"]:
            fixed["resourceType"] = "namespace"  # MUST be singular for kubectl_create
            # For namespace creation, only name is needed (not namespace field)
            if "namespace" in fixed:
                fixed.pop("namespace")
    
    # Handle kubectl_get specific logic
    if tool_schema and tool_schema.get("name") == "kubectl_get":
        # For kubectl_get, use PLURAL forms
        resource_mappings_get = {
            "namespace": "namespaces",
            "pod": "pods",
            "node": "nodes",
            "deployment": "deployments",
            "service": "services",
            "configmap": "configmaps",
            "secret": "secrets"
        }
        
        if fixed.get("resourceType") in resource_mappings_get:
            fixed["resourceType"] = resource_mappings_get[fixed["resourceType"]]
        
        # Handle "all namespaces" request
        if fixed.get("resourceType") in ["pods", "services", "deployments", "secrets", "configmaps", "nodes"]:
            if "namespace" not in fixed or fixed.get("namespace") == "all":
                fixed["allNamespaces"] = True
                fixed.pop("namespace", None)
        
        # Handle explicit "all" namespace request
        if fixed.get("namespace") == "all":
            fixed["allNamespaces"] = True
            fixed.pop("namespace", None)
    
    # Handle kubectl_delete specific logic
    if tool_schema and tool_schema.get("name") == "kubectl_delete":
        # For kubectl_delete, use SINGULAR form
        if fixed.get("resourceType") in ["namespaces"]:
            fixed["resourceType"] = "namespace"
    
    # Handle kubectl_describe specific logic  
    if tool_schema and tool_schema.get("name") == "kubectl_describe":
        # For kubectl_describe, typically uses singular
        resource_mappings_describe = {
            "namespaces": "namespace",
            "pods": "pod",
            "nodes": "node",
            "deployments": "deployment",
            "services": "service",
            "configmaps": "configmap",
            "secrets": "secret"
        }
        
        if fixed.get("resourceType") in resource_mappings_describe:
            fixed["resourceType"] = resource_mappings_describe[fixed["resourceType"]]
    
    # Remove None values
    fixed = {k: v for k, v in fixed.items() if v is not None}
    
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

def intelligent_server_selection(query: str, available_servers: list) -> Optional[Dict[str, Any]]:
    """Intelligently select the best MCP server based on query analysis."""
    if not available_servers:
        return None
    
    query_lower = query.lower()
    
    # Use Gemini for intelligent server selection if available
    if GEMINI_AVAILABLE:
        try:
            server_descriptions = []
            for server in available_servers:
                tools = list_mcp_tools(server["url"])
                tool_names = [t.get("name", "") for t in tools if t.get("name")]
                server_info = f"- {server['name']} (URL: {server['url']}): Tools available: {', '.join(tool_names[:5])}{'...' if len(tool_names) > 5 else ''}"
                server_descriptions.append(server_info)
            
            servers_info = "\n".join(server_descriptions)
            
            model = genai.GenerativeModel(GEMINI_MODEL)
            prompt = f"""
            Analyze the user query and determine which MCP server is most appropriate.
            
            User Query: "{query}"
            
            Available Servers:
            {servers_info}
            
            Instructions:
            - Choose the server whose tools best match the user's request
            - Consider the server name, available tools, and query context
            - Return ONLY the server name in this format: "SERVER_NAME"
            - If no clear match, choose the first server
            
            Respond with just the server name:
            """
            
            response = model.generate_content(prompt)
            selected_server_name = response.text.strip().strip('"').strip("'")
            
            # Find the server by name
            for server in available_servers:
                if server["name"].lower() == selected_server_name.lower():
                    return server
            
        except Exception as e:
            st.error(f"AI server selection failed: {str(e)}")
    
    # Fallback: keyword-based selection
    server_scores = []
    
    for server in available_servers:
        score = 0
        server_name_lower = server["name"].lower()
        tools = list_mcp_tools(server["url"])
        tool_names = [t.get("name", "").lower() for t in tools if t.get("name")]
        
        # Score based on server name keywords
        if "kube" in server_name_lower or "kubernetes" in server_name_lower:
            if any(word in query_lower for word in ["pod", "node", "namespace", "deployment", "service", "secret", "configmap", "cluster", "kube", "k8s"]):
                score += 10
        if "jenkins" in server_name_lower:
            if any(word in query_lower for word in ["jenkins", "job", "build", "pipeline", "ci/cd"]):
                score += 10
        if "argo" in server_name_lower:
            if any(word in query_lower for word in ["argo", "gitops", "application", "sync", "deploy"]):
                score += 10
        
        # Score based on tool names matching query
        for tool_name in tool_names:
            if tool_name in query_lower:
                score += 5
        
        server_scores.append((server, score))
    
    # Return server with highest score, or first server if tie/no score
    server_scores.sort(key=lambda x: x[1], reverse=True)
    return server_scores[0][0] if server_scores else available_servers[0]

def find_tool_by_name(tools: List[Dict], tool_name: str) -> Optional[Dict]:
    """Find a tool by its exact name."""
    for tool in tools:
        if tool.get("name") == tool_name:
            return tool
    return None

def intelligent_tool_selection(query: str, server_url: str) -> Dict[str, Any]:
    """Intelligently select the best tool and arguments for the query."""
    tools = list_mcp_tools(server_url)
    if not tools:
        return {"tool": None, "args": None, "explanation": "No tools available on this server"}
    
    tool_descriptions = get_tool_descriptions(server_url)
    
    if GEMINI_AVAILABLE:
        try:
            model = genai.GenerativeModel(GEMINI_MODEL)
            prompt = f"""
            Analyze the user query and map it to the most appropriate MCP tool.
            
            User Query: "{query}"
            
            Available Tools and Their Capabilities:
            {tool_descriptions}
            
            CRITICAL RULES FOR KUBERNETES:
            1. kubectl_create: ALWAYS use SINGULAR resourceType
               - "create namespace abc" → {{tool: "kubectl_create", args: {{resourceType: "namespace", name: "abc"}}}}
               - "create pod xyz" → {{tool: "kubectl_create", args: {{resourceType: "pod", name: "xyz"}}}}
            
            2. kubectl_get: ALWAYS use PLURAL resourceType
               - "get all pods" → {{tool: "kubectl_get", args: {{resourceType: "pods", allNamespaces: true}}}}
               - "get all namespaces" → {{tool: "kubectl_get", args: {{resourceType: "namespaces"}}}}
               - "get pods in default" → {{tool: "kubectl_get", args: {{resourceType: "pods", namespace: "default"}}}}
            
            3. kubectl_describe: Use SINGULAR resourceType
               - "describe node xyz" → {{tool: "kubectl_describe", args: {{resourceType: "node", name: "xyz"}}}}
            
            4. kubectl_delete: Use SINGULAR resourceType
               - "delete namespace abc" → {{tool: "kubectl_delete", args: {{resourceType: "namespace", name: "abc"}}}}
            
            5. Tool names must EXACTLY match the available tools
            6. Only include parameters that are defined in the tool schema
            
            REMEMBER: 
            - kubectl_create = SINGULAR (namespace, pod, deployment)
            - kubectl_get = PLURAL (namespaces, pods, deployments)
            - kubectl_describe = SINGULAR (namespace, pod, deployment)
            - kubectl_delete = SINGULAR (namespace, pod, deployment)
            
            Return ONLY valid JSON in this exact format:
            {{
                "tool": "exact_tool_name",
                "args": {{
                    "param1": "value1",
                    "param2": "value2"
                }},
                "explanation": "Brief explanation of choice"
            }}
            
            If no tool matches, set tool to null.
            
            Respond with ONLY the JSON:
            """
            
            response = model.generate_content(prompt)
            text = response.text.strip()
            
            # Remove markdown code blocks if present
            if text.startswith("```"):
                text = re.sub(r'^```json?\s*', '', text)
                text = re.sub(r'\s*```$', '', text)
            
            # Extract JSON from response
            parsed = None
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = _extract_json_from_text(text)
            
            if parsed and parsed.get("tool"):
                # Find the actual tool schema
                tool_schema = find_tool_by_name(tools, parsed["tool"])
                
                # Sanitize arguments with tool schema
                parsed["args"] = sanitize_args(parsed.get("args") or {}, tool_schema)
                return parsed
                
        except Exception as e:
            st.error(f"AI tool selection failed: {str(e)}")
    
    # Fallback: pattern-based tool selection
    query_lower = query.lower()
    
    # Check for specific operations
    if "create" in query_lower and "namespace" in query_lower:
        # Extract namespace name
        words = query.split()
        namespace_name = None
        for i, word in enumerate(words):
            if word.lower() in ["namespace", "ns"] and i + 1 < len(words):
                namespace_name = words[i + 1]
                break
        
        if namespace_name:
            tool_schema = find_tool_by_name(tools, "kubectl_create")
            return {
                "tool": "kubectl_create",
                "args": sanitize_args({"resourceType": "namespace", "name": namespace_name}, tool_schema),
                "explanation": f"Creating namespace '{namespace_name}' using kubectl_create"
            }
    
    # Check each tool for matches
    for tool in tools:
        tool_name = tool.get("name", "").lower()
        tool_description = tool.get("description", "").lower()
        
        # Simple keyword matching
        tool_keywords = tool_name.split('_') + tool_description.split()
        
        for keyword in tool_keywords:
            if len(keyword) > 3 and keyword in query_lower:
                # Try to extract basic arguments
                args = extract_arguments_from_query(query, tool.get("inputSchema", {}))
                return {
                    "tool": tool["name"],
                    "args": sanitize_args(args, tool),
                    "explanation": f"Matched tool '{tool['name']}' based on keyword '{keyword}'"
                }
    
    return {"tool": None, "args": None, "explanation": "No suitable tool found for this query"}

def extract_arguments_from_query(query: str, input_schema: dict) -> dict:
    """Extract arguments from natural language query based on tool schema."""
    args = {}
    properties = input_schema.get("properties", {})
    query_lower = query.lower()
    
    for param_name, param_schema in properties.items():
        param_type = param_schema.get("type", "string")
        
        # Look for parameter values in query
        if param_name in ["resourceType", "resource"]:
            # Extract resource type
            resource_patterns = [
                r'\b(pods?|deployments?|services?|namespaces?|nodes?|secrets?|configmaps?)\b'
            ]
            
            for pattern in resource_patterns:
                matches = re.findall(pattern, query_lower)
                if matches:
                    resource = matches[0]
                    # Pluralize if needed
                    if not resource.endswith('s'):
                        resource = resource + 's'
                    args[param_name] = resource
                    break
        
        elif param_name == "name":
            # Extract name - look for words after keywords
            patterns = [
                r'namespace[:\s]+([\w-]+)',
                r'named?[:\s]+([\w-]+)',
                r'called[:\s]+([\w-]+)',
            ]
            
            for pattern in patterns:
                match = re.search(pattern, query_lower)
                if match:
                    args[param_name] = match.group(1)
                    break
        
        elif param_name == "namespace":
            # Extract namespace
            namespace_match = re.search(r'(?:in|namespace)[:\s]+([\w-]+)', query_lower)
            if namespace_match:
                args[param_name] = namespace_match.group(1)
        
        elif param_name == "allNamespaces":
            if any(phrase in query_lower for phrase in ["all namespaces", "all namespace", "across namespaces", "cluster-wide"]):
                args[param_name] = True
        
        elif param_name == "allResources":
            if "all resources" in query_lower:
                args[param_name] = True
    
    return args

# ---------------- GEMINI FUNCTIONS ----------------
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
            "- If operation succeeded, confirm it clearly with ✅\n"
            "- If error occurred, explain what went wrong in simple terms and suggest what user can do.\n"
            "- If cluster name or size was inferred, mention that explicitly.\n"
            "- If cluster size = 1, say: 'This appears to be a minimal/single-node cluster.'\n"
            "- NEVER show JSON, code, or internal errors to user unless asked.\n"
            "- Be helpful, friendly, and precise.\n"
            "- For namespace creation success, say: 'Namespace created successfully!'\n"
            "- For namespace already exists error, say: 'This namespace already exists.'"
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
            if "already exists" in error_msg.lower() or "AlreadyExists" in error_msg:
                return "✅ This namespace already exists in the cluster."
            elif "forbidden" in error_msg.lower() or "permission" in error_msg.lower():
                return "❌ I don't have permission to create namespaces. Please check your Kubernetes RBAC permissions."
            else:
                return f"❌ Couldn't create the namespace. Error: {error_msg}"
        
        if "cluster" in user_input.lower():
            return "❌ I couldn't retrieve the cluster information. Please check if the MCP server is running."
        return f"❌ An issue occurred: {error_msg}"
    
    result = raw_response.get("result", {})
    
    # Check for successful operations
    if isinstance(result, dict):
        # Handle namespace creation success
        if "create" in user_input.lower() and "namespace" in user_input.lower():
            if result.get("metadata") and result["metadata"].get("name"):
                return f"✅ Namespace '{result['metadata']['name']}' created successfully!"
        
        # Handle list operations
        if "items" in result:
            items = result["items"]
            count = len(items)
            
            if count > 0:
                resource_type = "resources"
                if "pod" in user_input.lower():
                    resource_type = "pods"
                elif "service" in user_input.lower():
                    resource_type = "services"
                elif "node" in user_input.lower():
                    resource_type = "nodes"
                elif "namespace" in user_input.lower():
                    resource_type = "namespaces"
                
                return f"✅ Found {count} {resource_type} in the cluster."
            else:
                return "No resources found for your query."
    
    # Generic success message
    if not raw_response.get("error"):
        return "✅ Operation completed successfully."
    
    return "Operation completed."

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

# ---------------- NEW: GENERAL Q&A WITH GEMINI ----------------
def answer_general_question(user_input: str) -> str:
    """Use Gemini to answer general questions when no tool is relevant."""
    if not GEMINI_AVAILABLE:
        return (
            "I can only answer infrastructure-related questions when tools are available. "
            "For general questions, please provide a valid Gemini API key."
        )
    
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        prompt = (
            f"You are a helpful DevOps and Kubernetes assistant named MaSa Bot. "
            f"Answer the following question clearly and concisely in a friendly tone:\n\n"
            f"Question: {user_input}\n\n"
            "Guidelines:\n"
            "- Keep answers under 3-4 sentences unless complex.\n"
            "- If the question is about Kubernetes, cloud, or infrastructure, be precise.\n"
            "- If unsure, say so politely.\n"
            "- Never mention internal systems or that you're an AI unless asked.\n"
            "- Respond in English."
        )
        response = model.generate_content(prompt)
        return getattr(response, "text", "I'm not sure how to answer that.").strip()
    except Exception as e:
        return f"Sorry, I couldn't process your question. Error: {str(e)}"

# ---------------- STREAMLIT APP ----------------
def main():
    st.set_page_config(page_title="MaSa Bot", page_icon="⚡", layout="wide")
    st.title("🤖 MaSa Bot")

    # Sidebar with settings
    with st.sidebar:
        st.header("⚙️ Settings")
        
        # Server discovery
        if st.button("🔄 Refresh Server List"):
            with st.spinner("Discovering MCP servers..."):
                st.session_state.available_servers = load_servers()
                st.session_state.server_tools_cache = {}  # Clear cache
                st.success(f"Found {len(st.session_state.available_servers)} servers")
        
        st.text_input("Gemini API Key", value=GEMINI_API_KEY[:20] + "...", disabled=True, type="password")
        
        if st.button("🗑️ Clear Chat History"):
            st.session_state.messages = []
            st.rerun()

    # Main chat interface
    st.subheader("Chat with your MCP Servers 🚀")
    
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
    
    # Intelligent server selection
    with st.spinner("🔍 Selecting the best server for your query..."):
        selected_server = intelligent_server_selection(user_prompt, st.session_state.available_servers)
    
    if not selected_server:
        error_msg = "No MCP servers available. Please check your servers.json file."
        st.session_state.messages.append({"role": "assistant", "content": error_msg})
        with st.chat_message("assistant"):
            st.error(error_msg)
        return
    
    # Show which server we're using
    server_info = f"**🤖 Selected Server:** {selected_server['name']}\n\n**🔧 Available Tools:**"
    tools = list_mcp_tools(selected_server["url"])
    if tools:
        tool_names = [f"`{tool['name']}`" for tool in tools[:8]]  # Show first 8 tools
        server_info += " " + ", ".join(tool_names)
        if len(tools) > 8:
            server_info += f" ... and {len(tools) - 8} more"
    else:
        server_info += " No tools found"
    
    st.session_state.messages.append({"role": "assistant", "content": server_info})
    with st.chat_message("assistant"):
        st.markdown(server_info)
    
    # Intelligent tool selection
    with st.spinner("🤔 Analyzing your request and selecting the right tool..."):
        decision = intelligent_tool_selection(user_prompt, selected_server["url"])
    
    explanation = decision.get("explanation", "Analyzing your request...")
    st.session_state.messages.append({"role": "assistant", "content": f"💡 {explanation}"})
    with st.chat_message("assistant"):
        st.markdown(f"💡 {explanation}")
    
    tool_name = decision.get("tool")
    tool_args = decision.get("args") or {}
    
    # Execute tool if one was selected
    if tool_name:
        with st.chat_message("assistant"):
            st.markdown(f"🔧 Executing `{tool_name}` with parameters: `{json.dumps(tool_args)}`...")
        
        # Call the tool
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
        # No tool selected → handle as general question
        with st.spinner("🧠 Thinking..."):
            general_answer = answer_general_question(user_prompt)
        
        st.session_state.messages.append({"role": "assistant", "content": general_answer})
        with st.chat_message("assistant"):
            st.markdown(general_answer)

if __name__ == "__main__":
    main()
