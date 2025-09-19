# ================= IMPORTS =================
import os
import json
import time
import requests
import streamlit as st
from dotenv import load_dotenv
from datetime import datetime, timezone
from typing import Optional, Dict, Any
import google.generativeai as genai
import re

# ================= CONFIG =================
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyAkqKd3Hc60Qf6N_3ZYj1eu_GtFzkMmMVQ")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

GEMINI_AVAILABLE = False
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        GEMINI_AVAILABLE = True
    except Exception:
        GEMINI_AVAILABLE = False

# ================= SERVER MANAGEMENT =================
def load_servers() -> list:
    """Load MCP servers from servers.json or fallback to default."""
    try:
        with open("servers.json") as f:
            data = json.load(f)
            return data.get("servers", []) or []
    except Exception:
        return [{
            "name": "default",
            "url": "http://127.0.0.1:3000/mcp",
            "description": "Fallback server"
        }]

servers = load_servers() or [{
    "name": "default",
    "url": "http://127.0.0.1:3000/mcp",
    "description": "Fallback server"
}]

# ================= HELPERS =================
def call_mcp_server(method: str,
                    params: Optional[Dict[str, Any]] = None,
                    server_url: Optional[str] = None,
                    timeout: int = 20) -> Dict[str, Any]:
    """Generic MCP server JSON-RPC call."""
    url = server_url or servers[0]["url"]
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
        res = requests.post(url, json=payload, headers=headers, timeout=timeout)
        res.raise_for_status()
        text = res.text.strip() if res.text else ""

        # Handle SSE style response
        if text.startswith("event:") or "data:" in text:
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("data:"):
                    payload_text = line[len("data:"):].strip()
                    try:
                        return json.loads(payload_text)
                    except Exception:
                        return {"result": payload_text}

        # Handle JSON response
        try:
            return res.json()
        except ValueError:
            return {"result": res.text}

    except requests.exceptions.RequestException as e:
        return {"error": f"MCP server request failed: {str(e)}"}

def list_mcp_tools(server_url: Optional[str] = None) -> list:
    """List available tools on MCP server."""
    resp = call_mcp_server("tools/list", server_url=server_url)
    if not isinstance(resp, dict):
        return []
    result = resp.get("result")
    if isinstance(result, dict):
        return result.get("tools", []) or []
    if isinstance(result, list):
        return result
    return []

def call_tool(name: str,
              arguments: dict,
              server_url: Optional[str] = None) -> Dict[str, Any]:
    """Call a tool on MCP server."""
    return call_mcp_server("tools/call", {
        "name": name,
        "arguments": arguments or {}
    }, server_url=server_url)

def sanitize_args(args: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Fix common argument issues before calling tools."""
    if not args:
        return {}
    fixed = dict(args)
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

# ================= GEMINI FUNCTIONS =================
def ask_gemini_for_tool_and_server(query: str,
                                   retries: int = 2) -> Dict[str, Any]:
    """Ask Gemini to select tool + server for query."""
    available_tools = []
    for s in servers:
        tools = list_mcp_tools(s["url"])
        available_tools.extend([t.get("name") for t in tools if t.get("name")])
    available_tools = list(set(available_tools))
    server_names = [s["name"] for s in servers]

    context_notes = ""
    if "last_known_cluster_name" in st.session_state:
        context_notes += f"\nUser previously interacted with cluster: {st.session_state['last_known_cluster_name']}"
    if "last_known_cluster_size" in st.session_state:
        context_notes += f"\nLast known cluster size: {st.session_state['last_known_cluster_size']} nodes"

    instruction = f"""
You are an AI assistant that maps a user's natural language query to an available MCP tool call.
User query: "{query}"
{context_notes}

Available servers: {json.dumps(server_names)}
Available tools (ONLY use these): {json.dumps(available_tools)}

RULES:
- NEVER invent tool names. Only use tools listed above.
- If user asks for "cluster name", and "kubectl_get" is available, use it on "nodes" or "namespaces" to infer name from metadata.
- If user asks for "cluster size", use "kubectl_get" with "nodes" and count items.
- If no direct tool exists, pick the closest one (e.g., get nodes to infer cluster info).
- Return STRICT JSON only:
{{"tool": "<tool_name_or_null>", "args": {{ ... }}, "server": "<server_name_or_null>", "explanation": "short natural language explanation"}}
If no suitable tool, set tool and server to null.
"""

    if not GEMINI_AVAILABLE:
        return {
            "tool": None,
            "args": None,
            "server": None,
            "explanation": "Gemini not configured; using fallback logic."
        }

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
                continue

            suggested_tool = parsed.get("tool")
            if suggested_tool and suggested_tool not in available_tools:
                parsed["tool"] = None
                parsed["explanation"] = f"Tool '{suggested_tool}' not available. Available: {available_tools}"

            parsed["args"] = sanitize_args(parsed.get("args") or {})
            return parsed

        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2)
                continue
            return {
                "tool": None,
                "args": None,
                "server": None,
                "explanation": f"Gemini error: {str(e)}"
            }

    return {
        "tool": None,
        "args": None,
        "server": None,
        "explanation": "Gemini failed after retries."
    }

def ask_gemini_answer(user_input: str, raw_response: dict) -> str:
    """Use Gemini to convert raw MCP response into human-friendly answer."""
    if not GEMINI_AVAILABLE:
        return generate_fallback_answer(user_input, raw_response)
    try:
        context_notes = ""
        if "last_known_cluster_name" in st.session_state:
            context_notes += f"\nPreviously known cluster: {st.session_state['last_known_cluster_name']}"
        if "last_known_cluster_size" in st.session_state:
            context_notes += f"\nPreviously known size: {st.session_state['last_known_cluster_size']} nodes"

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
        extract_and_store_cluster_info(user_input, answer)
        return answer
    except Exception as e:
        return generate_fallback_answer(user_input, raw_response)

def generate_fallback_answer(user_input: str, raw_response: dict) -> str:
    """Generate human-friendly answer without Gemini."""
    if "error" in raw_response:
        error_msg = raw_response["error"]
        if "cluster" in user_input.lower():
            return "I couldn't retrieve the cluster name right now. I can try to infer it from nodes if you'd like!"
        return f"Sorry, I ran into an issue: {error_msg}"

    result = raw_response.get("result", {})
    if isinstance(result, dict) and "items" in result:
        items = result["items"]
        count = len(items)
        if "node" in str(result).lower() or "cluster size" in user_input.lower():
            if count == 1:
                node_name = items[0].get("metadata", {}).get("name", "unknown")
                return f"This is a single-node cluster. Node: {node_name}"
            else:
                return f"The cluster has {count} nodes."

    if isinstance(result, dict) and "items" in result and len(result["items"]) > 0:
        first_item = result["items"][0]
        if "metadata" in first_item:
            name = first_item["metadata"].get("name", "")
            if name:
                cluster_name = name.split(".")[0] if "." in name else name
                if "cluster name" in user_input.lower():
                    return f"I inferred the cluster name from the node name: *{cluster_name}*"

    return "Here's what I found:\n" + json.dumps(result, indent=2)

def extract_and_store_cluster_info(user_input: str, answer: str):
    """Extract cluster name/size from Gemini answer and store in session."""
    try:
        if "cluster name" in user_input.lower():
            patterns = [
                r"cluster[^\w]*(\w+)",
                r"name[^\w][:\-]?[^\w](\w+)",
                r"\\(\w+)\\",
            ]
            for pattern in patterns:
                match = re.search(pattern, answer, re.IGNORECASE)
                if match:
                    cluster_name = match.group(1).strip()
                    st.session_state["last_known_cluster_name"] = cluster_name
                    break
        if "cluster size" in user_input.lower() or "how many nodes" in user_input.lower():
            numbers = re.findall(r'\b\d+\b', answer)
            if numbers:
                st.session_state["last_known_cluster_size"] = int(numbers[0])
    except Exception:
        pass

# ================= SPECIAL HANDLING =================
def handle_special_queries(user_input: str, server_url: str) -> Optional[Dict[str, Any]]:
    """Handle special cases: 'cluster name' and 'all resources'."""
    user_lower = user_input.lower()
    
    # Case 1: cluster name
    if "cluster name" in user_lower:
        node_resp = call_tool("kubectl_get", {"resourceType": "nodes", "format": "json"}, server_url=server_url)
        if node_resp and "result" in node_resp:
            items = node_resp["result"].get("items", [])
            if items:
                first_node = items[0].get("metadata", {}).get("name", "unknown-cluster")
                cluster_hint = first_node.split(".")[0] if "." in first_node else first_node
                st.session_state["last_known_cluster_name"] = cluster_hint
                return {"result": {"inferred_cluster_name": cluster_hint}}
        return {"error": "Could not infer cluster name."}

    # Case 2: fetch all resources
    if "all" in user_lower or "all resources" in user_lower:
        resp = call_tool("kubectl_get", {"resourceType": "all", "allNamespaces": True, "format": "json"}, server_url=server_url)
        return resp

    return None

# ================= STREAMLIT APP =================
def main():
    st.set_page_config(page_title="MCP Chat Assistant", page_icon="⚡", layout="wide")
    st.title("🤖 Masa Bot Assistant")

    if "messages" not in st.session_state:
        st.session_state["messages"] = []

    for msg in st.session_state["messages"]:
        with st.chat_message(msg.get("role", "assistant")):
            st.markdown(msg.get("content", ""))

    user_prompt = st.chat_input("Ask Kubernetes or ArgoCD something...")
    if not user_prompt:
        return

    st.session_state["messages"].append({"role": "user", "content": user_prompt})
    st.experimental_rerun()

    # Choose first server as default
    server_url = servers[0]["url"]

    # ===== SPECIAL HANDLING FOR CLUSTER NAME OR ALL =====
    special_resp = handle_special_queries(user_prompt, server_url)
    if special_resp:
        resp = special_resp
    else:
        # normal flow: ask Gemini for tool + args
        decision = ask_gemini_for_tool_and_server(user_prompt)
        tool_name = decision.get("tool")
        tool_args = decision.get("args") or {}
        tool_server_name = decision.get("server")
        if tool_server_name:
            matching = [s for s in servers if s["name"] == tool_server_name]
            if matching:
                server_url = matching[0]["url"]

        if tool_name:
            resp = call_tool(tool_name, tool_args, server_url=server_url)
        else:
            resp = {"error": "No suitable tool found for your query."}

    answer = ask_gemini_answer(user_prompt, resp)
    st.session_state["messages"].append({"role": "assistant", "content": answer})
    st.experimental_rerun()

if __name__ == "__main__":
    main()
