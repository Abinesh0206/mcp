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
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
DEFAULT_GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://3.80.48.199:8080/mcp")


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

# -------------------- JSON-RPC Client --------------------
def mcp_json_rpc_request(method: str, params: Optional[Dict] = None, request_id: int = 1):
    """Make a JSON-RPC request to the MCP server"""
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params or {}
    }

    try:
        response = requests.post(
            MCP_SERVER_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {"error": f"JSON-RPC request failed: {str(e)}"}

def list_mcp_tools():
    """Get list of available tools from MCP server"""
    response = mcp_json_rpc_request("tools/list")
    if "result" in response and "tools" in response["result"]:
        return response["result"]["tools"]
    return []

def call_mcp_tool(tool_name: str, arguments: Dict):
    """Call a specific MCP tool"""
    response = mcp_json_rpc_request("tools/call", {
        "name": tool_name,
        "arguments": arguments
    })
    return response

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
        "Accept": "application/json, text/event-stream, /"
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

    # Set default namespace for pods if not specified
    if fixed.get("resourceType") == "pods" and "namespace" not in fixed:
        fixed["namespace"] = "default"

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
                 "resource" in query_lower) and
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
            response = call_tool(server_url, "kubectl_get", {
                "resourceType": resource_type,
                "allNamespaces": True
            })

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
def call_gemini_raw(prompt_text: str, model: str = DEFAULT_GEMINI_MODEL,
                api_key: Optional[str] = None, timeout: int = 60):
    if not api_key:
        return False, "Gemini API key not set (GEMINI_API_KEY)"

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {"contents": [{"role": "user", "parts": [{"text": prompt_text}]}]}

    try:
        response = requests.post(url, json=payload, timeout=timeout)
        response.raise_for_status()
        return True, response.json()
    except Exception as e:
        return False, str(e)

def gemini_answer_text_from_response(response_data: dict) -> str:
    try:
        if "candidates" in response_data:
            candidate = response_data.get("candidates", [])[0]
            parts = candidate.get("content", {}).get("parts") or candidate.get("content") or []
            if parts and isinstance(parts[0], dict):
                return parts[0].get("text", "").strip()
        return json.dumps(response_data)[:2000]
    except Exception:
        return str(response_data)

def ask_gemini(user_text: str, system_prompt: str):
    prompt = system_prompt + "\n\nUser: " + user_text + "\nAssistant:"
    success, response = call_gemini_raw(
        prompt,
        model=st.session_state.get("gemini_model", DEFAULT_GEMINI_MODEL),
        api_key=GEMINI_API_KEY
    )

    if not success:
        return False, f"[Gemini error] {response}"

    return True, gemini_answer_text_from_response(response)

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
- Only choose from the tools above.
- If the query clearly maps to a tool, return tool + args in JSON.
- If the user asks for "all resources" or "everything in cluster", use kubectl_get with appropriate arguments.
- If unsure, set tool=null and args=null.

Respond ONLY in strict JSON:
{{"tool": "<tool_name>" | null, "args": {{}} | null, "explanation": "Short explanation"}}
"""
    if not GEMINI_AVAILABLE:
        # Fallback logic for common queries
        query_lower = query.lower()
        if "all resources" in query_lower or "everything" in query_lower or "all" in query_lower:
            return {
                "tool": "kubectl_get",
                "args": {"resourceType": "all", "allNamespaces": True},
                "explanation": "User wants to see all resources in cluster"
            }
        elif "pods" in query_lower:
            return {
                "tool": "kubectl_get",
                "args": {"resourceType": "pods", "allNamespaces": True},
                "explanation": "User wants to see all pods"
            }
        elif "services" in query_lower or "svc" in query_lower:
            return {
                "tool": "kubectl_get",
                "args": {"resourceType": "services", "allNamespaces": True},
                "explanation": "User wants to see all services"
            }
        elif "secrets" in query_lower:
            return {
                "tool": "kubectl_get",
                "args": {"resourceType": "secrets", "allNamespaces": True},
                "explanation": "User wants to see all secrets"
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

def ask_gemini_answer(user_prompt: str, raw_response: dict) -> str:
    """Use Gemini to convert raw MCP response into human-friendly answer."""
    if not GEMINI_AVAILABLE:
        return generate_fallback_answer(user_prompt, raw_response)

    try:
        context_notes = ""
        if st.session_state.last_known_cluster_name:
            context_notes += f"\nPreviously known cluster: {st.session_state.last_known_cluster_name}"
        if st.session_state.last_known_cluster_size:
            context_notes += f"\nPreviously known size: {st.session_state.last_known_cluster_size} nodes"

        model = genai.GenerativeModel(GEMINI_MODEL)
        prompt = (
            f"User asked: {user_prompt}\n"
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
        extract_and_store_cluster_info(user_prompt, answer)

        return answer

    except Exception as e:
        return generate_fallback_answer(user_prompt, raw_response)

def generate_fallback_answer(user_prompt: str, raw_response: dict) -> str:
    """Generate human-friendly answer without Gemini."""
    if "error" in raw_response:
        error_msg = raw_response["error"]
        if "cluster" in user_prompt.lower():
            return "I couldn't retrieve the cluster information right now. Please check if the MCP server is running and accessible."
        return f"Sorry, I encountered an issue: {error_msg}"

    result = raw_response.get("result", {})

    # Handle different response formats
    if isinstance(result, dict):
        # Kubernetes-style responses with items
        if "items" in result:
            items = result["items"]
            count = len(items)

            if "node" in user_prompt.lower() or "cluster size" in user_prompt.lower():
                if count == 1:
                    node_name = items[0].get("metadata", {}).get("name", "unknown")
                    return f"This is a single-node cluster. The node is named: {node_name}"
                else:
                    return f"The cluster has {count} nodes."

            if "namespace" in user_prompt.lower():
                namespaces = [item.get("metadata", {}).get("name", "unnamed") for item in items]
                if namespaces:
                    return f"Found {count} namespaces:\n" + "\n".join([f"• {ns}" for ns in namespaces])
                else:
                    return "No namespaces found."

            if "pod" in user_prompt.lower():
                pods = [f"{item.get('metadata', {}).get('name', 'unnamed')} in {item.get('metadata', {}).get('namespace', 'default')} namespace" for item in items]
                if pods:
                    return f"Found {count} pods:\n" + "\n".join([f"• {pod}" for pod in pods])
                else:
                    return "No pods found."

            if "secret" in user_prompt.lower():
                secrets = [f"{item.get('metadata', {}).get('name', 'unnamed')} in {item.get('metadata', {}).get('namespace', 'default')} namespace" for item in items]
                if secrets:
                    return f"Found {count} secrets:\n" + "\n".join([f"• {secret}" for secret in secrets])
                else:
                    return "No secrets found."

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

    # Generic fallback
    if result:
        return f"Operation completed successfully. Result: {json.dumps(result, indent=2)}"

    return "Operation completed successfully, but no data was returned."

def extract_and_store_cluster_info(user_prompt: str, answer: str):
    """Extract cluster name/size from Gemini answer and store in session."""
    try:
        # Extract cluster name
        if "cluster name" in user_prompt.lower():
            patterns = [
                r"cluster[^\w]*([\w-]+)",       # matches things like "cluster demo-cluster"
                r"name[^\w][:\-]?[^\w]([\w-]+)",# matches patterns like "name: demo" or "name-demo"
                r"\(([\w-]+)\)",                # matches a bolded name inside parentheses
            ]

            for pattern in patterns:
                match = re.search(pattern, answer, re.IGNORECASE)
                if match:
                    cluster_name = match.group(1).strip()
                    st.session_state.last_known_cluster_name = cluster_name
                    break

        # Extract cluster size
        if "cluster size" in user_prompt.lower() or "how many nodes" in user_prompt.lower():
            numbers = re.findall(r'\b\d+\b', answer)
            if numbers:
                st.session_state.last_known_cluster_size = int(numbers[0])
    except Exception:
        pass  # silent fail

# -------------------- Tool Functions --------------------
def mcp_list_jobs():
    result = call_mcp_tool("list_jobs", {})
    if "result" in result:
        # Extract the actual job list from the response
        if "content" in result["result"]:
            # Try to parse the content as JSON if it's a string
            if isinstance(result["result"]["content"], str):
                try:
                    content = json.loads(result["result"]["content"])
                    if "jobs" in content:
                        return content
                except json.JSONDecodeError:
                    # If it's not JSON, return as is
                    return result["result"]
            elif "jobs" in result["result"]["content"]:
                return result["result"]["content"]
        return result["result"]
    return result

def mcp_get_job(name: str):
    result = call_mcp_tool("get_job", {"name": name})
    if "result" in result:
        return result["result"]
    return result

def mcp_create_job(name: str, config_xml: str):
    result = call_mcp_tool("create_job", {"name": name, "config_xml": config_xml})
    if "result" in result:
        return result["result"]
    return result

def mcp_delete_job(name: str):
    result = call_mcp_tool("delete_job", {"name": name})
    if "result" in result:
        return result["result"]
    return result

def mcp_enable_job(name: str):
    result = call_mcp_tool("enable_job", {"name": name})
    if "result" in result:
        return result["result"]
    return result

def mcp_disable_job(name: str):
    result = call_mcp_tool("disable_job", {"name": name})
    if "result" in result:
        return result["result"]
    return result

def mcp_rename_job(name: str, new_name: str):
    result = call_mcp_tool("rename_job", {"name": name, "new_name": new_name})
    if "result" in result:
        return result["result"]
    return result

def mcp_trigger_build(job_name: str, parameters: Optional[Dict] = None):
    result = call_mcp_tool("trigger_build", {"jobName": job_name, "parameters": parameters or {}})
    if "result" in result:
        return result["result"]
    return result

def mcp_stop_build(job_name: str, build_number: int):
    result = call_mcp_tool("stop_build", {"job_name": job_name, "build_number": build_number})
    if "result" in result:
        return result["result"]
    return result

def mcp_get_build_info(job_name: str, build_number: int):
    result = call_mcp_tool("get_build_info", {"job_name": job_name, "build_number": build_number})
    if "result" in result:
        return result["result"]
    return result

def mcp_get_build_logs(job_name: str, build_number: int):
    result = call_mcp_tool("get_build_logs", {"job_name": job_name, "build_number": build_number})
    if "result" in result:
        return result["result"]
    return result

def mcp_get_job_config(job_name: str):
    result = call_mcp_tool("get_job_config", {"name": job_name})
    if "result" in result:
        return result["result"]
    return result

def mcp_update_job_config(job_name: str, config_xml: str):
    result = call_mcp_tool("update_job_config", {"name": job_name, "config_xml": config_xml})
    if "result" in result:
        return result["result"]
    return result

# -------------------- Enhanced Job Wizard --------------------
def generate_job_config_complete(job_data: Dict) -> str:
    """Generate complete job configuration XML based on job data"""
    name = job_data.get("name", "")
    job_type = job_data.get("type", "freestyle")
    description = job_data.get("description", "")
    pipeline_type = job_data.get("pipeline_type", "script")
    pipeline_script = job_data.get("pipeline_script", "")
    repo_url = job_data.get("repo_url", "")
    branch = job_data.get("branch", "main")
    credentials_id = job_data.get("credentials_id", "")
    jenkinsfile_path = job_data.get("jenkinsfile_path", "Jenkinsfile")

    if job_type == "pipeline":
        if pipeline_type == "scm":
            # Pipeline from SCM
            return f"""<?xml version='1.1' encoding='UTF-8'?>
<flow-definition plugin="workflow-job@2.46">
<description>{description}</description>
<definition class="org.jenkinsci.plugins.workflow.cps.CpsScmFlowDefinition" plugin="workflow-cps@2.93">
    <scm class="hudson.plugins.git.GitSCM" plugin="git@4.11.4">
    <configVersion>2</configVersion>
    <userRemoteConfigs>
        <hudson.plugins.git.UserRemoteConfig>
        <url>{repo_url}</url>
        <credentialsId>{credentials_id}</credentialsId>
        </hudson.plugins.git.UserRemoteConfig>
    </userRemoteConfigs>
    <branches>
        <hudson.plugins.git.BranchSpec>
        <name>{branch}</name>
        </hudson.plugins.git.BranchSpec>
    </branches>
    <doGenerateSubmoduleConfigurations>false</doGenerateSubmoduleConfigurations>
    <submoduleCfg class="list"/>
    <extensions/>
    </scm>
    <scriptPath>{jenkinsfile_path}</scriptPath>
    <lightweight>false</lightweight>
</definition>
<triggers/>
<disabled>false</disabled>
</flow-definition>"""
        else:
            # Pipeline script
            if not pipeline_script:
                # Generate basic pipeline script
                pipeline_script = f"""pipeline {{
    agent any
    description '{description}'
    stages {{
        stage('Example') {{
            steps {{
                echo 'Hello World'
            }}
        }}
    }}
}}"""

            return f"""<?xml version='1.1' encoding='UTF-8'?>
<flow-definition plugin="workflow-job@2.46">
<description>{description}</description>
<definition class="org.jenkinsci.plugins.workflow.cps.CpsFlowDefinition" plugin="workflow-cps@2.93">
    <script>{pipeline_script}</script>
    <sandbox>true</sandbox>
</definition>
<triggers/>
<disabled>false</disabled>
</flow-definition>"""
    else:
        # Freestyle job (simplified)
        return f"""<?xml version='1.1' encoding='UTF-8'?>
<project>
<description>{description}</description>
<builders>
    <hudson.tasks.Shell>
    <command>echo "Hello World"</command>
    </hudson.tasks.Shell>
</builders>
</project>"""

def start_job_wizard():
    """Start the enhanced job creation wizard"""
    st.session_state["job_wizard"] = {
        "step": "name",
        "data": {}
    }
    return "Let's create a new Jenkins job! Please enter the Job Name."

def continue_job_wizard(user_text: str):
    """Continue the job creation wizard based on current step"""
    wizard = st.session_state.get("job_wizard", {})
    step = wizard.get("step")
    data = wizard.get("data", {})

    # Helper function to update wizard state
    def update_wizard(next_step, response):
        wizard["step"] = next_step
        wizard["data"] = data
        st.session_state["job_wizard"] = wizard
        return response

    if step == "name":
        data["name"] = user_text.strip()
        return update_wizard("type", "Got it. What type of job? (freestyle/pipeline)")

    elif step == "type":
        job_type = user_text.strip().lower()
        if job_type not in ["freestyle", "pipeline"]:
            return "Please enter either 'freestyle' or 'pipeline'."

        data["type"] = job_type
        return update_wizard("description", "Enter a description for the job:")

    elif step == "description":
        data["description"] = user_text.strip()

        if data["type"] == "pipeline":
            return update_wizard("pipeline_type", "Select pipeline type: (script/scm)")
        else:
            return update_wizard("confirm", "Ready to create job. Type 'yes' to confirm:")

    elif step == "pipeline_type":
        pipeline_type = user_text.strip().lower()
        if pipeline_type not in ["script", "scm"]:
            return "Please enter either 'script' or 'scm'."

        data["pipeline_type"] = pipeline_type

        if pipeline_type == "scm":
            return update_wizard("repo_url", "Enter the Git repository URL:")
        else:
            return update_wizard("pipeline_script", "Enter the Pipeline script (Groovy) or type 'generate' to auto-generate:")

    elif step == "repo_url":
        data["repo_url"] = user_text.strip()
        return update_wizard("branch", "Enter the branch name (default: main):")

    elif step == "branch":
        data["branch"] = user_text.strip() or "main"
        return update_wizard("credentials", "Enter credentials ID (GitHub username + token):")

    elif step == "credentials":
        data["credentials_id"] = user_text.strip()
        return update_wizard("jenkinsfile_path", "Enter the Jenkinsfile path (default: Jenkinsfile):")

    elif step == "jenkinsfile_path":
        data["jenkinsfile_path"] = user_text.strip() or "Jenkinsfile"
        return update_wizard("confirm", "Ready to create job. Type 'yes' to confirm:")

    elif step == "pipeline_script":
        if user_text.strip().lower() == "generate":
            # Auto-generate pipeline script
            data["pipeline_script"] = ""
        else:
            data["pipeline_script"] = user_text.strip()
        return update_wizard("confirm", "Ready to create job. Type 'yes' to confirm:")

    elif step == "confirm":
        if user_text.strip().lower() != "yes":
            st.session_state.pop("job_wizard", None)
            return "Job creation cancelled."

        # Generate config and create job
        config_xml = generate_job_config_complete(data)
        result = mcp_create_job(data["name"], config_xml)
        st.session_state.pop("job_wizard", None)

        if "error" in result:
            return f"Failed to create job: {result['error']}"
        else:
            return f"Job '{data['name']}' created successfully!"

    return "Something went wrong in the wizard flow."

# -------------------- Enhanced Natural Language Processing --------------------
def extract_job_creation_params(query: str) -> Dict:
    """Extract job creation parameters from natural language query"""
    # Use Gemini to parse the query and extract parameters
    system_prompt = """Extract Jenkins job creation parameters from the user's query.
    Return a JSON object with these fields:
    {
        "name": "job_name",
        "type": "freestyle|pipeline",
        "description": "job_description",
        "pipeline_type": "script|scm",
        "repo_url": "repository_url",
        "branch": "branch_name",
        "credentials_id": "credentials_id",
        "jenkinsfile_path": "jenkinsfile_path",
        "pipeline_script": "pipeline_script"
    }

    Fill only the fields that are mentioned in the query. For missing fields, use empty strings or empty arrays."""

    success, response = ask_gemini(query, system_prompt)

    if not success:
        return {}

    try:
        return json.loads(response)
    except json.JSONDecodeError:
        return {}

# -------------------- Natural Language Processing --------------------
def extract_job_name(query: str, action: str) -> str:
    """Extract job name from natural language query"""
    query = query.replace(action, "").strip()

    patterns = [
        r'job\s+([^\s]+)',
        r'the\s+([^\s]+)\s+job',
        r'([^\s]+)\s+job',
        r'build\s+([^\s]+)',
        r'([^\s]+)\s+build',
        r'delete\s+([^\s]+)',
        r'([^\s]+)\s+delete',
        r'enable\s+([^\s]+)',
        r'disable\s+([^\s]+)',
        r'rename\s+([^\s]+)',
        r'stop\s+([^\s]+)',
        r'config\s+([^\s]+)',
        r'([^\s]+)\s+config'
    ]

    for pattern in patterns:
        match = re.search(pattern, query, re.IGNORECASE)
        if match:
            candidate = match.group(1).lower()
            if candidate in ["job", "jenkins"]:  # 🚫 not a real job name
                return ""
            return match.group(1)

    words = query.split()
    if words:
        candidate = words[-1].lower()
        if candidate in ["job", "jenkins"]:  # 🚫 skip dummy words
            return ""
        return words[-1]

    return ""

def human_readable_json(data: dict) -> str:
    """Convert a JSON dictionary into a human-readable string."""
    if not data:
        return "No information available."

    lines = []
    for key, value in data.items():
        # Convert snake_case to Title Case
        title = key.replace("_", " ").title()
        if isinstance(value, dict):
            # Recursive formatting for nested dicts
            nested = human_readable_json(value)
            lines.append(f"{title}:\n{nested}")
        elif isinstance(value, list):
            lines.append(f"{title}: {', '.join(map(str, value))}")
        else:
            lines.append(f"{title}: {value}")
    return "\n".join(lines)



def extract_build_number(query: str) -> tuple:
    """Extract job name and build number from query"""
    # Look for patterns like "build 123 of job X"
    patterns = [
        r'build\s+(\d+)\s+of\s+job\s+([^\s]+)',
        r'job\s+([^\s]+)\s+build\s+(\d+)',
        r'build\s+(\d+)\s+for\s+([^\s]+)',
        r'([^\s]+)\s+build\s+(\d+)',
        r'stop\s+build\s+(\d+)\s+of\s+([^\s]+)',
        r'logs\s+for\s+build\s+(\d+)\s+of\s+([^\s]+)',
        r'info\s+for\s+build\s+(\d+)\s+of\s+([^\s]+)'
    ]

    for pattern in patterns:
        match = re.search(pattern, query, re.IGNORECASE)
        if match:
            return match.group(2), int(match.group(1))

    # If no build number specified, return the job name and "last"
    job_name = extract_job_name(query, "build")
    return job_name, "last"

def extract_rename_params(query: str) -> tuple:
    """Extract old and new job names from rename query"""
    patterns = [
        r'rename\s+([^\s]+)\s+to\s+([^\s]+)',
        r'rename\s+job\s+([^\s]+)\s+to\s+([^\s]+)',
        r'change\s+name\s+of\s+([^\s]+)\s+to\s+([^\s]+)'
    ]

    for pattern in patterns:
        match = re.search(pattern, query, re.IGNORECASE)
        if match:
            return match.group(1), match.group(2)

    return "", ""

def understand_query_intent(query: str) -> Dict:
    """Use Gemini to understand the intent of the query"""
    system_prompt = """You are a Jenkins assistant. Analyze the user's query and determine what Jenkins operation they want to perform.
    Available operations:
    - list_jobs: List all Jenkins jobs
    - get_job: Get details about a specific job
    - create_job: Create a new Jenkins job
    - delete_job: Delete a job
    - enable_job: Enable a job
    - disable_job: Disable a job
    - rename_job: Rename a job
    - trigger_build: Trigger a build for a job
    - stop_build: Stop a running build
    - get_build_info: Get information about a specific build
    - get_build_logs: Get logs for a specific build
    - get_job_config: Get the configuration XML for a job
    - update_job_config: Update the configuration XML for a job

    Respond with a JSON object in this format:
    {
        "operation": "operation_name",
        "parameters": {
            "job_name": "name_of_job",
            "new_job_name": "new_name_for_job" (for rename),
            "build_number": 123 (or "last" if not specified),
            "config_xml": "xml_content" (for update config)
        }
    }

    If the operation is not clear, respond with:
    {
        "operation": "unknown",
        "parameters": {}
    }"""

    success, response = ask_gemini(query, system_prompt)

    if not success:
        return {"operation": "unknown", "parameters": {}}

    try:
        # Try to parse the response as JSON
        return json.loads(response)
    except json.JSONDecodeError:
        # If it's not JSON, try to extract the operation from the text
        query_lower = query.lower()

        if "list" in query_lower and "job" in query_lower:
            return {"operation": "list_jobs", "parameters": {}}
        elif "create" in query_lower and "job" in query_lower:
            return {"operation": "create_job", "parameters": {}}
        elif "delete" in query_lower and "job" in query_lower:
            job_name = extract_job_name(query, "delete job")
            return {"operation": "delete_job", "parameters": {"job_name": job_name}}
        elif "enable" in query_lower and "job" in query_lower:
            job_name = extract_job_name(query, "enable job")
            return {"operation": "enable_job", "parameters": {"job_name": job_name}}
        elif "disable" in query_lower and "job" in query_lower:
            job_name = extract_job_name(query, "disable job")
            return {"operation": "disable_job", "parameters": {"job_name": job_name}}
        elif "rename" in query_lower and "job" in query_lower:
            old_name, new_name = extract_rename_params(query)
            return {"operation": "rename_job", "parameters": {"job_name": old_name, "new_job_name": new_name}}
        elif ("trigger" in query_lower or "start" in query_lower) and "build" in query_lower:
            job_name = extract_job_name(query, "trigger build")
            return {"operation": "trigger_build", "parameters": {"job_name": job_name}}
        elif "stop" in query_lower and "build" in query_lower:
            job_name, build_number = extract_build_number(query)
            return {"operation": "stop_build", "parameters": {"job_name": job_name, "build_number": build_number}}
        elif "log" in query_lower or "console" in query_lower:
            job_name, build_number = extract_build_number(query)
            return {"operation": "get_build_logs", "parameters": {"job_name": job_name, "build_number": build_number}}
        elif "info" in query_lower or "status" in query_lower:
            job_name, build_number = extract_build_number(query)
            return {"operation": "get_build_info", "parameters": {"job_name": job_name, "build_number": build_number}}
        elif "config" in query_lower and "get" in query_lower:
            job_name = extract_job_name(query, "get job config")
            return {"operation": "get_job_config", "parameters": {"job_name": job_name}}
        elif "config" in query_lower and ("update" in query_lower or "change" in query_lower):
            job_name = extract_job_name(query, "update job config")
            return {"operation": "update_job_config", "parameters": {"job_name": job_name}}
        elif "detail" in query_lower or "about" in query_lower:
            job_name = extract_job_name(query, "get job details")
            return {"operation": "get_job", "parameters": {"job_name": job_name}}
        else:
            return {"operation": "unknown", "parameters": {}}

# -------------------- Ask Jenkins Question --------------------
def ask_jenkins_question(user_text: str):
    query = user_text.lower().strip()

    # Enhanced job creation with parameters extraction
    if any(word in query for word in ["create job", "make job", "new job", "add job"]):
        # Try to extract parameters from the query
        params = extract_job_creation_params(user_text)

        if params and "name" in params and params["name"]:
            # Direct creation with extracted parameters
            config_xml = generate_job_config_complete(params)
            result = mcp_create_job(params["name"], config_xml)

            if "error" in result:
                return f"Failed to create job: {result['error']}"
            else:
                return f"Job '{params['name']}' created successfully with extracted parameters!"
        else:
            # Start interactive wizard
            return start_job_wizard()

    # First, try to understand the intent using Gemini
    intent = understand_query_intent(query)
    operation = intent.get("operation")
    parameters = intent.get("parameters", {})

    # List jobs
    if operation == "list_jobs":
        result = mcp_list_jobs()
        if "error" in result:
            return f"Error: {result['error']}"

        # Handle different response formats
        jobs = []
        if "jobs" in result:
            jobs = result["jobs"]
        elif "content" in result and "jobs" in result["content"]:
            jobs = result["content"]["jobs"]
        elif isinstance(result, list):
            jobs = result

        if not jobs:
            return "No jobs found in Jenkins."

        response = "Jenkins Jobs:\n"
        for job in jobs:
            if isinstance(job, dict):
                name = job.get("name", "Unknown")
                status = "SUCCESS" if job.get("color") == "blue" else "FAILED" if job.get("color") == "red" else "UNSTABLE/OTHER"
                description = job.get("description", "")

                response += f"- {name} [{status}]"
                if description:
                    response += f" - {description}"
                response += "\n"
            else:
                # If job is just a string (name only)
                response += f"- {job}\n"

        return response

    # Job details
    elif operation == "get_job":
        job_name = parameters.get("job_name", "")
        if not job_name:
            job_name = extract_job_name(query, "get job details")

        if not job_name:
            return "Please specify which job you want details for. For example: 'get details for job abc'"

        result = mcp_get_job(job_name)

        if "error" in result:
            return f"Error getting job details: {result['error']}"

        return f"Job Details for {job_name}:\n\n{human_readable_json(result)}"

    # Create job
    elif operation == "create_job":
        return start_job_wizard()

    # Delete job
    elif operation == "delete_job":
        job_name = parameters.get("job_name", "")
        if not job_name:
            job_name = extract_job_name(query, "delete job")

        if not job_name:
            # 👇 Instead of error, ask user for job name
            return "Please enter the job name you want to delete."

        result = mcp_delete_job(job_name)

        if "error" in result:
            return f"Error deleting job: {result['error']}"

        return f"Job '{job_name}' deleted successfully!"


    # Enable job
    elif operation == "enable_job":
        job_name = parameters.get("job_name", "")
        if not job_name:
            job_name = extract_job_name(query, "enable job")

        if not job_name:
            return "Please specify which job you want to enable. For example: 'enable job abc'"

        result = mcp_enable_job(job_name)

        if "error" in result:
            return f"Error enabling job: {result['error']}"

        return f"Job '{job_name}' enabled successfully!"

    # Disable job
    elif operation == "disable_job":
        job_name = parameters.get("job_name", "")
        if not job_name:
            job_name = extract_job_name(query, "disable job")

        if not job_name:
            return "Please specify which job you want to disable. For example: 'disable job abc'"

        result = mcp_disable_job(job_name)

        if "error" in result:
            return f"Error disabling job: {result['error']}"

        return f"Job '{job_name}' disabled successfully!"

    # Rename job
    elif operation == "rename_job":
        current_name = parameters.get("current_name", "")
        new_name = parameters.get("new_name", "")

        if not current_name or not new_name:
            current_name, new_name = extract_rename_params(query)
            if not current_name or not new_name:
                return "Please specify both the current job name and the new job name. For example: 'rename job abc to xyz'"

        result = mcp_rename_job(current_name, new_name)

        if "error" in result:
            return f"Error renaming job: {result['error']}"

        return f"Job '{current_name}' renamed to '{new_name}' successfully!"


    # Trigger build
    elif operation == "trigger_build":
        job_name = parameters.get("job_name", "")
        if not job_name:
            job_name = extract_job_name(query, "trigger build")

        if not job_name:
            return "Please specify which job you want to build. For example: 'trigger build for job abc'"

        result = mcp_trigger_build(job_name)

        if "error" in result:
            return f"Error triggering build: {result['error']}"

        return f"Triggered build for '{job_name}'"

    # Stop build
    elif operation == "stop_build":
        job_name = parameters.get("job_name", "")
        build_number = parameters.get("build_number", "last")

        if not job_name:
            job_name, build_number = extract_build_number(query)

        if build_number == "last":
            job_info = mcp_get_job(job_name)
            if "error" in job_info:
                return f"Error getting job info: {job_info['error']}"

            if "lastBuild" in job_info and job_info["lastBuild"] is not None:
                build_number = job_info["lastBuild"]["number"]
            else:
                return f"No builds found for job {job_name}"

        result = mcp_stop_build(job_name, int(build_number))

        if "error" in result:
            return f"Error stopping build: {result['error']}"

        return f"Build {build_number} for job '{job_name}' stopped successfully!"

    # Build info
    elif operation == "get_build_info":
        job_name = parameters.get("job_name", "")
        build_number = parameters.get("build_number", "last")

        if not job_name:
            job_name, build_number = extract_build_number(query)

        if build_number == "last":
            job_info = mcp_get_job(job_name)
            if "error" in job_info:
                return f"Error getting job info: {job_info['error']}"

            if "lastBuild" in job_info and job_info["lastBuild"] is not None:
                build_number = job_info["lastBuild"]["number"]
            else:
                return f"No builds found for job {job_name}"

        result = mcp_get_build_info(job_name, int(build_number))

        if "error" in result:
            return f"Error getting build info: {result['error']}"

        return f"Build Info for {job_name} #{build_number}:\n\n{human_readable_json(result)}"

    # Build logs
    elif operation == "get_build_logs":
        job_name = parameters.get("job_name", "")
        build_number = parameters.get("build_number", "last")

        if not job_name:
            job_name, build_number = extract_build_number(query)

        if build_number == "last":
            job_info = mcp_get_job(job_name)
            if "error" in job_info:
                return f"Error getting job info: {job_info['error']}"

            if "lastBuild" in job_info and job_info["lastBuild"] is not None:
                build_number = job_info["lastBuild"]["number"]
            else:
                return f"No builds found for job {job_name}"

        result = mcp_get_build_logs(job_name, int(build_number))

        if "error" in result:
            return f"Error getting build logs: {result['error']}"

        logs = result.get("text", "No logs available")
        return f"Build Logs for {job_name} #{build_number}:\n\n{logs[:1000]}...\n"

    # Get job config
    elif operation == "get_job_config":
        job_name = parameters.get("job_name", "")
        if not job_name:
            job_name = extract_job_name(query, "get job config")

        if not job_name:
            return "Please specify which job you want to get config for. For example: 'get config for job abc'"

        result = mcp_get_job_config(job_name)

        if "error" in result:
            return f"Error getting job config: {result['error']}"

        config_xml = result.get("content", "No config available")
        return f"Job Config for {job_name}:\nxml\n{config_xml[:1000]}...\n"

    # Update job config
    elif operation == "update_job_config":
        job_name = parameters.get("job_name", "")
        if not job_name:
            job_name = extract_job_name(query, "update job config")

        if not job_name:
            return "Please specify which job you want to update config for. For example: 'update config for job abc'"

        # For now, we'll just return a message about this operation
        # In a real implementation, you'd need to handle the XML content
        return f"To update the configuration for job '{job_name}', please provide the new XML configuration."

    # Default - use Gemini for conversational responses
    system_prompt = """You are a Jenkins assistant. Answer clearly using Jenkins MCP server context.
    Available operations:
    - Job Management: Create, delete, enable/disable, rename jobs
    - Job Building: Trigger builds, build with parameters, stop builds
    - Job Information: Get job info, build info, build logs
    - Job Configuration: Get and update job configurations

    Keep responses concise and helpful."""

    success, response = ask_gemini(user_text, system_prompt)
    return response if success else f"[Gemini error] {response}"

# ---------------- STREAMLIT APP ----------------
def main():
    st.set_page_config(page_title="MCP Chat Assistant", page_icon="⚡", layout="wide")
    st.title("🤖 MaSaOps Bot")

    # Sidebar with settings
    with st.sidebar:
        st.header("⚙ Settings")

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
    server_info = f"🤖 Using server: *{selected_server['name']}*"
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

    if tool_name:
        response = call_tool(selected_server["url"], tool_name, tool_args)
        final_answer = ask_gemini_answer(user_prompt, response)
        st.session_state.messages.append({"role": "assistant", "content": final_answer})

    # Initialize session state
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "gemini_model" not in st.session_state:
        st.session_state.gemini_model = DEFAULT_GEMINI_MODEL

    # Display chat messages
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

        # Check if we're in a wizard flow
        if "job_wizard" in st.session_state:
            response = continue_job_wizard(user_prompt)
        else:
            # Process regular query
            with st.spinner("Thinking..."):
                response = ask_jenkins_question(user_prompt)

        st.session_state.messages.append({"role": "assistant", "content": response})
        st.rerun()


    # Execute tool if one was selected
    if tool_name:
        with st.chat_message("assistant"):
            st.markdown(f"🔧 Executing {tool_name}...")

        # Special handling for "all resources" request
        if (user_prompt.lower().strip() in ["show me all resources in cluster", "get all resources", "all resources"] or
            ("all" in user_prompt.lower() and "resource" in user_prompt.lower())):
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
            "*For Kubernetes:*\n"
            "- \"List all namespaces\"\n"
            "- \"Show running pods\"\n"
            "- \"Get cluster nodes\"\n"
            "- \"Show all services\"\n"
            "- \"List all secrets\"\n"
            "- \"Show all resources in cluster\"\n\n"
            "*For Jenkins:*\n"
            "- \"List all jobs\"\n"
            "- \"Show build status\"\n\n"
            "*For ArgoCD:*\n"
            "- \"List applications\"\n"
            "- \"Show application status\"\n\n"
            "Or try being more specific about what you'd like to see!"
        )

        st.session_state.messages.append({"role": "assistant", "content": helpful_response})
        with st.chat_message("assistant"):
            st.markdown(helpful_response)

if __name__ == "__main__":
    main()
