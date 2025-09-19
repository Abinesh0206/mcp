import os
import json
import time
import requests
import streamlit as st
from dotenv import load_dotenv
import google.generativeai as genai
from datetime import datetime, timezone
from typing import Optional, Dict, Any
import re

# ---------------- CONFIG ----------------
load_dotenv()
API_URL = os.getenv("API_URL", "http://54.227.78.211:8080")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyBYRBa7dQ5atjlHk7e3IOdZBdo6OOcn2Pk")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

# Configure Gemini if available
GEMINI_AVAILABLE = False
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        GEMINI_AVAILABLE = True
    except Exception:
        GEMINI_AVAILABLE = False

# Load servers list (names only). Client will compose URL as API_URL + "/mcp?target=<name>"
def load_servers() -> list:
    try:
        with open("servers.json") as f:
            data = json.load(f)
        return data.get("servers", [])
    except Exception:
        return [{"name": "jenkins", "description": "Jenkins"}, 
                {"name": "kubernetes", "description": "Kubernetes"}, 
                {"name": "argocd", "description": "ArgoCD"}]

SERVERS = load_servers()
SERVER_NAMES = [s["name"] for s in SERVERS]

# Initialize session state
if "current_server" not in st.session_state:
    first_server_name = SERVERS[0]["name"]
    st.session_state["current_server"] = f"{API_URL}/mcp?target={first_server_name}"

if "session" not in st.session_state:
    st.session_state.session = None
    st.session_state.username = None
    st.session_state.access = []
    st.session_state.messages = []
    st.session_state.last_known_cluster_name = None
    st.session_state.last_known_cluster_size = None

def get_current_server_url():
    return st.session_state.get(
        "current_server",
        f"{API_URL}/mcp?target={SERVERS[0]['name']}"
    )

# ---------------- HELPERS ----------------
def gateway_call(target: str, method: str, params: Optional[Dict[str, Any]] = None, 
                 session_id: Optional[str] = None, timeout: int = 20) -> Dict[str, Any]:
    """Call Gateway /mcp with session_id and JSON-RPC body"""
    url = f"{API_URL}/mcp?target={target}"
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or {}
    }
    if session_id:
        body["session_id"] = session_id
    try:
        r = requests.post(url, json=body, timeout=timeout)
        try:
            return r.json()
        except Exception:
            return {"error": f"Non-JSON response: {r.text}", "status_code": r.status_code}
    except Exception as e:
        return {"error": str(e)}

def list_mcp_tools(target: str):
    """Fetch available MCP tools for a specific target."""
    resp = gateway_call(target, "tools/list", session_id=st.session_state.session)
    if not isinstance(resp, dict):
        return []
    # Some MCP servers return {"result": {"tools":[...]}} or {"result": [...]}
    result = resp.get("result")
    if isinstance(result, dict):
        return result.get("tools", [])
    if isinstance(result, list):
        return result
    return []

def call_tool(target: str, name: str, arguments: dict):
    """Execute MCP tool by name with arguments. Returns parsed response dict."""
    if not name or not isinstance(arguments, dict):
        return {"error": "Invalid tool name or arguments"}
    return gateway_call(target, "tools/call", 
                       {"name": name, "arguments": arguments}, 
                       session_id=st.session_state.session)

def sanitize_args(args: dict):
    """Fix arguments before sending to MCP tools."""
    if not args:
        return {}

    fixed = args.copy()
    if "resource" in fixed and "resourceType" not in fixed:
        fixed["resourceType"] = fixed.pop("resource")

    if fixed.get("resourceType") == "pods" and "namespace" not in fixed:
        fixed["namespace"] = "default"

    if fixed.get("namespace") == "all":
        fixed["allNamespaces"] = True
        fixed.pop("namespace", None)

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

# ---------------- GEMINI FUNCTIONS ----------------
def ask_gemini_for_tool_decision(query: str, target: str):
    """Use Gemini to map user query -> MCP tool + arguments."""
    tools = list_mcp_tools(target)
    tool_names = [t["name"] for t in tools]

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
- If unsure, set tool=null and args=null.

Respond ONLY in strict JSON:
{{"tool": "<tool_name>" | null, "args": {{}} | null, "explanation": "Short explanation"}}
"""
    if not GEMINI_AVAILABLE:
        return {"tool": None, "args": None, "explanation": "Gemini not configured; fallback to chat reply."}
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(instruction)
        text = response.text.strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = _extract_json_from_text(text) or {"tool": None, "args": None, "explanation": f"Gemini invalid response: {text}"}
        parsed["args"] = sanitize_args(parsed.get("args") or {})
        return parsed
    except Exception as e:
        return {"tool": None, "args": None, "explanation": f"Gemini error: {str(e)}"}

def ask_gemini_answer(user_input: str, raw_response: dict) -> str:
    """Use Gemini to convert raw MCP response into human-friendly answer."""
    if not GEMINI_AVAILABLE:
        # Fallback: try to extract cluster name/size manually
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
        if "cluster" in user_input.lower():
            return "I couldn't retrieve the cluster name right now. This might be because the cluster resource type isn't directly supported. But I can try to infer it from nodes or namespaces if you'd like!"
        return f"Sorry, I ran into an issue: {error_msg.split('MCP error')[-1].strip() if 'MCP error' in error_msg else error_msg}"

    result = raw_response.get("result", {})

    # Handle node list for cluster size
    if isinstance(result, dict) and "items" in result:
        items = result["items"]
        count = len(items)
        if "node" in str(result).lower() or "cluster size" in user_input.lower():
            if count == 1:
                node_name = items[0].get("metadata", {}).get("name", "unknown")
                return f"This is a single-node cluster. The node is named: {node_name}"
            else:
                return f"The cluster has {count} nodes."

    # Try to infer cluster name from node name
    if isinstance(result, dict) and "items" in result and len(result["items"]) > 0:
        first_item = result["items"][0]
        if "metadata" in first_item:
            name = first_item["metadata"].get("name", "")
            if name:
                # Heuristic: if node name has dots, cluster name is prefix
                cluster_name = name.split(".")[0] if "." in name else name
                if "cluster" in user_input.lower() and "name" in user_input.lower():
                    return f"I inferred the cluster name from the node name: *{cluster_name}*"

    # Generic fallback
    return "Here's what I found:\n" + json.dumps(result, indent=2)

def extract_and_store_cluster_info(user_input: str, answer: str):
    """Extract cluster name/size from Gemini answer and store in session."""
    try:
        # Extract cluster name
        if "cluster name" in user_input.lower():
            # Simple pattern: "cluster: xxx" or "name: xxx"
            patterns = [
                r"cluster[^\w]*(\w+)",
                r"name[^\w][:\-]?[^\w](\w+)",
                r"\*(\w+)\*",  # bolded name
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

    # Sidebar profile + logout
    with st.sidebar:
        st.header("👤 Profile")
        if st.session_state.session:
            st.write(f"*Username:* {st.session_state.username}")
            st.write(f"*Access:* {', '.join(st.session_state.access) if st.session_state.access else 'None'}")
            if st.button("Logout"):
                st.session_state.session = None
                st.session_state.username = None
                st.session_state.access = []
                st.session_state.messages = []
                st.rerun()
        else:
            st.write("Not logged in")

        st.title("MasaBot Settings")
        st.markdown("*Providers & Keys*")
        st.text_input("Gemini API Key", value=(GEMINI_API_KEY or ""), disabled=True)

        models = [GEMINI_MODEL, "gemini-1.0", "gemini-1.5-pro"]
        gemini_model = st.selectbox("Gemini model", options=models, index=0)
        st.session_state["gemini_model"] = gemini_model

        if st.button("Clear chat history"):
            st.session_state.messages = []
            st.rerun()

    # Login form
    if not st.session_state.session:
        st.subheader("Login")
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        if st.button("Login"):
            resp = requests.post(f"{API_URL}/login", json={"username": username, "password": password})
            if resp.status_code == 200:
                data = resp.json()
                st.session_state.session = data.get("session_id")
                st.session_state.username = data.get("username")
                st.session_state.access = data.get("access", [])
                st.success(f"Logged in as {st.session_state.username}. Access: {', '.join(st.session_state.access)}")
                st.rerun()
            else:
                try:
                    st.error(resp.json().get("detail", "Login failed"))
                except Exception:
                    st.error("Login failed")

        st.info("Seeded users")
        return
    
    st.subheader("What's on your mind today🤔?")

    # chat history
    for msg in st.session_state.messages:
        role = msg.get("role", "assistant")
        with st.chat_message(role):
            st.markdown(msg.get("content", ""))

    user_prompt = st.chat_input("Ask anything about MCP")
    if not user_prompt:
        return

    # Store user message
    st.session_state.messages.append({"role": "user", "content": user_prompt})
    st.chat_message("user").markdown(user_prompt)

    # Very simple heuristic decision (no Gemini required). If prompt contains backend name, pick it.
    selected_backend = None
    for name in SERVER_NAMES:
        if name in user_prompt.lower():
            selected_backend = name
            break

    if not selected_backend:
        # fallback: if only one access available, use it
        if len(st.session_state.access) == 1:
            selected_backend = st.session_state.access[0]

    if not selected_backend:
        answer = "Could not determine backend from query. Please specify (jenkins / kubernetes / argocd) or select backend in UI."
        st.session_state.messages.append({"role": "assistant", "content": answer})
        st.chat_message("assistant").markdown(answer)
        return

    # Check RBAC locally before asking gateway
    if selected_backend not in st.session_state.access:
        answer = f"🚫 Access denied: you are not allowed to call {selected_backend}."
        st.session_state.messages.append({"role": "assistant", "content": answer})
        st.chat_message("assistant").markdown(answer)
        return

    # Use Gemini to determine the best tool and arguments
    decision = ask_gemini_for_tool_decision(user_prompt, selected_backend)
    explanation = f"💡 {decision.get('explanation', 'I’m figuring out how to help you...')}"
    st.session_state.messages.append({"role": "assistant", "content": explanation})
    st.chat_message("assistant").markdown(explanation)

    tool_name = decision.get("tool")
    tool_args = decision.get("args") or {}

    # Execute tool
    if tool_name:
        display_args = json.dumps(tool_args, indent=2, ensure_ascii=False)
        st.chat_message("assistant").markdown(
            f"🔧 I'll use {tool_name} to help you. Here's what I'm asking the system:\n```json\n{display_args}\n```"
        )

        resp = call_tool(selected_backend, tool_name, tool_args)

        # Smart fallback for cluster name inference
        if "cluster name" in user_prompt.lower() and (resp.get("error") or not resp):
            st.chat_message("assistant").markdown("📌 Let me try to infer the cluster name from available nodes...")
            node_resp = call_tool(selected_backend, "kubectl_get", {"resourceType": "nodes", "format": "json"})
            if node_resp and not node_resp.get("error"):
                items = node_resp.get("result", {}).get("items", [])
                if items:
                    first_node = items[0].get("metadata", {}).get("name", "unknown-cluster")
                    cluster_hint = first_node.split(".")[0] if "." in first_node else first_node
                    st.session_state.last_known_cluster_name = cluster_hint
                    resp = {"result": {"inferred_cluster_name": cluster_hint}}
                    st.chat_message("assistant").markdown(f"✅ I inferred the cluster name: *{cluster_hint}*")

        # Smart handling for cluster size
        if "cluster size" in user_prompt.lower() and tool_name == "kubectl_get" and tool_args.get("resourceType") == "nodes":
            if not resp.get("error") and isinstance(resp.get("result"), dict):
                items = resp["result"].get("items", [])
                node_count = len(items)
                st.session_state.last_known_cluster_size = node_count
                if node_count == 1:
                    node_name = items[0].get("metadata", {}).get("name", "unknown")
                    resp["result"]["_note"] = f"Single-node cluster. Node: {node_name}"

        # Generate final natural language answer
        if not resp or "error" in resp:
            final_answer = ask_gemini_answer(user_prompt, resp)
        else:
            final_answer = ask_gemini_answer(user_prompt, resp)

        st.session_state.messages.append({"role": "assistant", "content": final_answer})
        st.chat_message("assistant").markdown(final_answer)

    else:
        # No tool selected — still try to give helpful answer
        helpful_response = (
            "I couldn't find a direct tool to answer your question, but here are some things you can ask:\n"
            "- \"What nodes are in the cluster?\"\n"
            "- \"List all namespaces\"\n"
            "- \"What's the cluster size?\"\n"
            "- \"Show Jenkins jobs\"\n"
            "- \"List ArgoCD applications\"\n"
            "\nOr try rephrasing your question!"
        )
        st.session_state.messages.append({"role": "assistant", "content": helpful_response})
        st.chat_message("assistant").markdown(helpful_response)

if __name__ == "__main__":
    main()
