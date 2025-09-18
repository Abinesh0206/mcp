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


# ================= CONFIG =================
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyApANXlk_-Pc0MrveXl6Umq0KLxdk5wr8c")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

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
        "Accept": "application/json, text/event-stream, */*"
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
    tool_names = [t.get("name") for s in servers for t in list_mcp_tools(s["url"])]
    server_names = [s["name"] for s in servers]

    # Inject context from session state if available
    context_notes = ""
    if "last_known_cluster_name" in st.session_state:
        context_notes += f"\nUser previously interacted with cluster: {st.session_state['last_known_cluster_name']}"
    if "last_known_cluster_size" in st.session_state:
        context_notes += f"\nLast known cluster size: {st.session_state['last_known_cluster_size']} nodes"

    instruction = f"""
You are an AI agent that maps a user's query to an MCP tool call and selects the best MCP server.
User query: "{query}"
{context_notes}

Available servers: {json.dumps(server_names)}
Available tools: {json.dumps(tool_names)}

Guidelines:
- If user asks for "cluster name", try "kubectl config current-context" or infer from node names or metadata.
- If user asks for "cluster size", use "kubectl get nodes" and count items.
- If direct command fails, suggest fallback (e.g., describe node for cluster hints).
- Return STRICT JSON only:
{{"tool": "<tool_name_or_null>", "args": {{ ... }}, "server": "<server_name_or_null>", "explanation": "short explanation"}}
If unsure, set tool and server to null.
"""

    if not GEMINI_AVAILABLE:
        return {
            "tool": None,
            "args": None,
            "server": None,
            "explanation": "Gemini not configured; fallback."
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
        return json.dumps(raw_response, indent=2)

    try:
        # Inject session context for consistency
        context_notes = ""
        if "last_known_cluster_name" in st.session_state:
            context_notes += f"\nPreviously known cluster: {st.session_state['last_known_cluster_name']}"
        if "last_known_cluster_size" in st.session_state:
            context_notes += f"\nPreviously known size: {st.session_state['last_known_cluster_size']} nodes"

        model = genai.GenerativeModel(GEMINI_MODEL)
        prompt = (
            f"User asked: {user_input}\n"
            f"Context: {context_notes}\n\n"
            f"Raw MCP response:\n{json.dumps(raw_response, indent=2)}\n\n"
            "Convert this into a detailed, human-friendly explanation. "
            "If it's a list, format with bullet points. "
            "If it's status, explain health and issues clearly. "
            "If error occurred, explain politely what went wrong and suggest next steps. "
            "If cluster name or size was inferred, mention that explicitly. "
            "If cluster size = 1, say: 'This appears to be a minimal/single-node cluster.'"
        )
        resp = model.generate_content(prompt)
        answer = getattr(resp, "text", str(resp)).strip()

        # Extract and store cluster info for future context
        if "cluster name" in user_input.lower() and "name" in answer.lower():
            # Simple extraction — can be enhanced with regex or NER
            if "cluster" in answer and ":" in answer:
                parts = answer.split(":")
                if len(parts) > 1:
                    cluster_name = parts[1].split("\n")[0].strip().strip('"').strip("'")
                    st.session_state["last_known_cluster_name"] = cluster_name

        if "cluster size" in user_input.lower() and "node" in answer.lower():
            import re
            numbers = re.findall(r'\b\d+\b', answer)
            if numbers:
                st.session_state["last_known_cluster_size"] = int(numbers[0])

        return answer

    except Exception as e:
        return f"Gemini error while post-processing: {str(e)}"


# ================= STREAMLIT APP =================
def main():
    st.set_page_config(page_title="MCP Chat Assistant", page_icon="⚡", layout="wide")
    st.title("🤖 Masa Bot Assistant")

    if "messages" not in st.session_state:
        st.session_state["messages"] = []

    # Render chat history
    for msg in st.session_state["messages"]:
        with st.chat_message(msg.get("role", "assistant")):
            st.markdown(msg.get("content", ""))

    # Chat input
    user_prompt = st.chat_input("Ask Kubernetes or ArgoCD something...")
    if not user_prompt:
        return

    # Store user message
    st.session_state["messages"].append({"role": "user", "content": user_prompt})
    st.chat_message("user").markdown(user_prompt)

    # Decision phase
    decision = ask_gemini_for_tool_and_server(user_prompt)
    explanation = f"💡 {decision.get('explanation', '')}" if decision.get("explanation") else "💡 Tool decision produced."
    st.session_state["messages"].append({"role": "assistant", "content": explanation})
    st.chat_message("assistant").markdown(explanation)

    # Resolve server URL
    server_url = next((s["url"] for s in servers if s["name"] == decision.get("server")), servers[0]["url"])
    tool_name = decision.get("tool")

    # Execute tool
    if tool_name:
        tool_args = decision.get("args") or {}
        st.chat_message("assistant").markdown(
            f"🔧 Executing *{tool_name}* on server {decision.get('server')} with arguments:\n```json\n{json.dumps(tool_args, indent=2)}\n```"
        )

        resp = call_tool(tool_name, tool_args, server_url=server_url)

        # Fallback for "cluster name" if kubectl get cluster fails
        if tool_name == "kubectl_get" and tool_args.get("resourceType") == "cluster" and resp.get("error"):
            st.chat_message("assistant").markdown("⚠️ `kubectl get cluster` failed. Trying fallback: `kubectl config current-context`...")
            fallback_resp = call_tool("kubectl_config", {"subcommand": "current-context"}, server_url=server_url)
            if not fallback_resp.get("error"):
                resp = fallback_resp
            else:
                # Try getting node name as cluster hint
                node_resp = call_tool("kubectl_get", {"resourceType": "nodes", "format": "json"}, server_url=server_url)
                if node_resp and not node_resp.get("error"):
                    items = node_resp.get("result", {}).get("items", [])
                    if items:
                        first_node = items[0].get("metadata", {}).get("name", "unknown-cluster")
                        cluster_hint = first_node.split(".")[0] if "." in first_node else first_node
                        resp = {"result": f"Inferred cluster name from node: {cluster_hint}"}
                        st.session_state["last_known_cluster_name"] = cluster_hint

        # Fallback for cluster size if only one node returned
        if tool_name == "kubectl_get" and tool_args.get("resourceType") == "nodes" and not resp.get("error"):
            result = resp.get("result", {})
            if isinstance(result, dict) and "items" in result:
                node_count = len(result.get("items", []))
                if node_count == 1:
                    # Store for context
                    st.session_state["last_known_cluster_size"] = node_count
                    # Enhance response
                    single_node = result["items"][0].get("metadata", {}).get("name", "unknown")
                    resp["result"]["_note"] = f"This is a single-node cluster. Node: {single_node}"

        if not resp or "error" in resp:
            final_answer = f"⚠️ No valid response received. {resp.get('error', 'Unknown error') if isinstance(resp, dict) else ''}"
        else:
            final_answer = ask_gemini_answer(user_prompt, resp)

        st.session_state["messages"].append({"role": "assistant", "content": final_answer})
        st.chat_message("assistant").markdown(final_answer)
    else:
        answer = "⚠️ No tool selected. Try again or check available MCP tools."
        st.session_state["messages"].append({"role": "assistant", "content": answer})
        st.chat_message("assistant").markdown(answer)


if __name__ == "__main__":
    main()
