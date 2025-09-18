# ================= IMPORTS =================
import os
import json
import time
import requests
import streamlit as st
from dotenv import load_dotenv
from typing import Optional, Dict, Any
import google.generativeai as genai
import re


# ================= CONFIG =================
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyDMcUvj_79LwDrimRhkfq6BUFTWttXc1BQ")
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
        if "" in text:
            for line in text.splitlines():
                line = line.strip()
                if line.startswith(""):
                    payload_text = line[len(""):].strip()
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
    except Exception as e:
        return {"error": f"Unexpected error: {str(e)}"}


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


# ================= VALIDATION HELPERS =================
def is_valid_k8s_response(response: dict) -> bool:
    """Check if response contains real, non-empty Kubernetes data."""
    if not response or "error" in response:
        return False
    result = response.get("result", {})
    if not result:
        return False

    if isinstance(result, dict) and "items" in result:
        return len(result.get("items", [])) > 0

    if isinstance(result, str) and len(result.strip()) > 0 and not result.strip().lower() in ["null", "none", "{}", "[]"]:
        return True

    if isinstance(result, dict) and len(result) > 0:
        return any(v not in [None, "", [], {}] for v in result.values())

    return False


def clean_cluster_name(name: str) -> str:
    """Clean and validate cluster name extracted from node."""
    if not name:
        return ""
    name = re.sub(r'^(ip-|node-|k8s-|kube-)', '', name, flags=re.IGNORECASE)
    name = name.split(".")[0]
    name = re.sub(r'[^a-zA-Z0-9\-]', '', name)
    return name.strip()[:50]


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
    BAD_NAMES = {"the", "unknown", "cluster", "null", "none", "undefined", ""}
    if "last_known_cluster_name" in st.session_state:
        cname = st.session_state['last_known_cluster_name']
        if cname and cname.lower() not in BAD_NAMES:
            context_notes += f"\nPreviously known cluster: {cname}"
    if "last_known_cluster_size" in st.session_state:
        csize = st.session_state['last_known_cluster_size']
        if isinstance(csize, int) and csize > 0:
            context_notes += f"\nCluster size: {csize} nodes"

    instruction = f"""
You are an AI assistant that maps a user's natural language query to an available MCP tool call.
User query: "{query}"
{context_notes}

Available servers: {json.dumps(server_names)}
Available tools (ONLY use these): {json.dumps(available_tools)}

RULES:
- NEVER invent tool names.
- If user asks for "cluster name", use "kubectl_get" on "nodes".
- If user says "show me all details", return tool: "kubectl_get" with args: {{"resourceType": "nodes"}}
- Return STRICT JSON only:
{{"tool": "<tool_name_or_null>", "args": {{ ... }}, "server": "<server_name_or_null>", "explanation": "short explanation"}}
"""

    if not GEMINI_AVAILABLE:
        return {
            "tool": None,
            "args": None,
            "server": None,
            "explanation": "Gemini not configured; using fallback."
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
                parsed["explanation"] = f"Tool '{suggested_tool}' not available."

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


def ask_gemini_answer(user_input: str, raw_response: dict, context: dict = None) -> str:
    """Use Gemini to convert raw MCP response into human-friendly answer."""
    if context is None:
        context = {}

    BAD_NAMES = {"the", "unknown", "cluster", "null", "none", "undefined", ""}
    if "last_known_cluster_name" in st.session_state:
        cname = st.session_state["last_known_cluster_name"]
        if isinstance(cname, str) and cname.lower().strip() in BAD_NAMES:
            del st.session_state["last_known_cluster_name"]

    if not GEMINI_AVAILABLE:
        return generate_fallback_answer(user_input, raw_response, context)

    try:
        context_notes = ""
        if "last_known_cluster_name" in st.session_state:
            cname = st.session_state['last_known_cluster_name']
            if cname and cname.lower() not in BAD_NAMES:
                context_notes += f"\nCluster: {cname}"
        if "last_known_cluster_size" in st.session_state:
            csize = st.session_state['last_known_cluster_size']
            if isinstance(csize, int) and csize > 0:
                context_notes += f"\nSize: {csize} nodes"

        model = genai.GenerativeModel(GEMINI_MODEL)
        prompt = (
            f"User asked: {user_input}\n"
            f"Context: {context_notes}\n\n"
            f"Raw system response:\n{json.dumps(raw_response, indent=2)}\n\n"
            "INSTRUCTIONS:\n"
            "- Respond in clear, natural English.\n"
            "- If cluster name was inferred, say so.\n"
            "- If error, explain politely.\n"
            "- NEVER show JSON or errors to user.\n"
            "- Be helpful and precise."
        )
        resp = model.generate_content(prompt)
        answer = getattr(resp, "text", str(resp)).strip()

        extract_and_store_cluster_info(user_input, answer)
        return answer

    except Exception as e:
        return generate_fallback_answer(user_input, raw_response, context)


def generate_fallback_answer(user_input: str, raw_response: dict, context: dict = None) -> str:
    """Generate human-friendly answer without Gemini — with forced cluster name inference."""
    if context is None:
        context = {}

    if "error" in raw_response:
        error_msg = raw_response["error"]
        if "kubectl" in error_msg or "forbidden" in error_msg.lower():
            return (
                "⚠️ Permission issue detected.\n"
                "I can't access cluster data. Please check MCP server RBAC permissions.\n"
                "Run: `kubectl auth can-i get nodes --all-namespaces` to verify."
            )
        return f"⚠️ Technical issue: {error_msg}"

    if not is_valid_k8s_response(raw_response):
        if "cluster" in user_input.lower() and ("name" in user_input.lower() or "details" in user_input.lower()):
            return (
                "🔍 I searched but found no cluster data. This usually means:\n"
                "• Cluster is empty\n"
                "• MCP server is misconfigured\n"
                "• Backend connection failed\n\n"
                "💡 Try asking: 'show me all details in my cluster' for full diagnostics."
            )
        return "📭 No data found in cluster."

    result = raw_response.get("result", {})

    # >>> FORCE CLUSTER NAME EXTRACTION <<<
    if "cluster name" in user_input.lower():
        if isinstance(result, dict) and "items" in result and len(result["items"]) > 0:
            first_item = result["items"][0]
            node_name = first_item.get("metadata", {}).get("name", "")
            if node_name:
                cluster_name = clean_cluster_name(node_name)
                if cluster_name:
                    st.session_state["last_known_cluster_name"] = cluster_name
                    return f"✅ Cluster name (inferred from node): **{cluster_name}**\n\nNode: `{node_name}`"
        if isinstance(result, dict) and "items" in result and len(result["items"]) > 0:
            first_ns = result["items"][0].get("metadata", {}).get("name", "")
            if first_ns:
                cluster_name = f"cluster-{first_ns}"
                st.session_state["last_known_cluster_name"] = cluster_name
                return f"✅ Cluster name (inferred from namespace): **{cluster_name}**"
        return "I couldn't find a node or namespace to infer the cluster name from. Please check if any resources exist."

    # Handle “show all cluster details”
    if context:
        summary = "📊 **Full Cluster Report**\n\n"

        # Cluster Name — FORCE INFERENCE
        cluster_name = None
        if "nodes" in context and "items" in context["nodes"] and len(context["nodes"]["items"]) > 0:
            first_node = context["nodes"]["items"][0].get("metadata", {}).get("name", "")
            if first_node:
                cluster_name = clean_cluster_name(first_node)
                st.session_state["last_known_cluster_name"] = cluster_name
                summary += f"🔹 **Cluster Name**: `{cluster_name}` (inferred from node `{first_node}`)\n"
        elif "namespaces" in context and "items" in context["namespaces"] and len(context["namespaces"]["items"]) > 0:
            first_ns = context["namespaces"]["items"][0].get("metadata", {}).get("name", "")
            cluster_name = f"cluster-{first_ns}"
            st.session_state["last_known_cluster_name"] = cluster_name
            summary += f"🔹 **Cluster Name**: `{cluster_name}` (inferred from namespace)\n"
        else:
            summary += "🔹 **Cluster Name**: `unknown-cluster` (no resources to infer from)\n"

        # Nodes
        if "nodes" in context:
            node_items = context["nodes"].get("items", [])
            summary += f"🔹 **Nodes**: {len(node_items)} total\n"
            for node in node_items[:3]:
                name = node.get("metadata", {}).get("name", "unknown")
                status = "Unknown"
                for cond in node.get("status", {}).get("conditions", []):
                    if cond.get("type") == "Ready" and cond.get("status") == "True":
                        status = "✅ Ready"
                        break
                summary += f"   • `{name}` ({status})\n"
            if len(node_items) > 3:
                summary += f"   • ... and {len(node_items) - 3} more\n"

        # Namespaces
        if "namespaces" in context:
            ns_items = context["namespaces"].get("items", [])
            summary += f"\n🔹 **Namespaces**: {len(ns_items)}\n"
            for ns in ns_items[:5]:
                name = ns.get("metadata", {}).get("name", "unknown")
                summary += f"   • `{name}`\n"
            if len(ns_items) > 5:
                summary += f"   • ... and {len(ns_items) - 5} more\n"

        # Pods
        if "pods" in context:
            pod_items = context["pods"].get("items", [])
            running = sum(1 for p in pod_items if p.get("status", {}).get("phase") == "Running")
            pending = sum(1 for p in pod_items if p.get("status", {}).get("phase") == "Pending")
            failed = sum(1 for p in pod_items if p.get("status", {}).get("phase") == "Failed")
            summary += f"\n🔹 **Pods**: {len(pod_items)} total | ✅ Running: {running} | ⏳ Pending: {pending} | ❌ Failed: {failed}\n"

        return summary.strip()

    # Generic fallback
    if isinstance(result, dict) and "items" in result:
        items = result["items"]
        if len(items) == 1:
            item = items[0]
            name = item.get("metadata", {}).get("name", "unknown")
            kind = result.get("kind", "Resource").replace("List", "")
            return f"Found 1 {kind.lower()}: `{name}`"
        else:
            return f"Found {len(items)} items. Ask for 'show me all details' for full report."

    return "Here's the raw data I received (ask for summary if needed):\n" + json.dumps(result, indent=2)[:800] + ("..." if len(str(result)) > 800 else "")


def extract_and_store_cluster_info(user_input: str, answer: str):
    """Extract cluster name/size from Gemini answer and store in session — only if valid."""
    try:
        BAD_NAMES = {"the", "unknown", "cluster", "null", "none", "undefined", ""}
        if "cluster name" in user_input.lower():
            patterns = [
                r"cluster[^\w]*(\w[\w\-]*)",
                r"name[^\w]*[:\-]?[^\w]*(\w[\w\-]*)",
                r"\*\*(\w[\w\-]*)\*\*",
                r"cluster\s*[:\-]?\s*(\w[\w\-]*)",
                r"inferred.*?[:\-]?\s*(\w[\w\-]*)"
            ]
            for pattern in patterns:
                match = re.search(pattern, answer, re.IGNORECASE)
                if match:
                    cluster_name = match.group(1).strip()
                    if cluster_name.lower() not in BAD_NAMES and len(cluster_name) >= 3:
                        st.session_state["last_known_cluster_name"] = cluster_name
                        break

        if any(kw in user_input.lower() for kw in ["cluster size", "how many nodes", "show"]):
            numbers = re.findall(r'\b(\d+)\b', answer)
            for num_str in numbers:
                num = int(num_str)
                if 1 <= num <= 10000:
                    st.session_state["last_known_cluster_size"] = num
                    break
    except Exception:
        pass


# ================= STREAMLIT APP =================
def main():
    st.set_page_config(page_title="MCP Chat Assistant", page_icon="⚡", layout="wide")
    st.title("🤖 Masa Bot Assistant")

    debug_mode = st.sidebar.checkbox("🛠 Debug Mode (Show Raw Data)")

    if "messages" not in st.session_state:
        st.session_state["messages"] = []

    for msg in st.session_state["messages"]:
        with st.chat_message(msg.get("role", "assistant")):
            st.markdown(msg.get("content", ""))
            if debug_mode and "raw" in msg:
                with st.expander("🔍 Debug: Raw Response"):
                    st.json(msg["raw"])

    user_prompt = st.chat_input("Ask Kubernetes or ArgoCD something...")
    if not user_prompt:
        return

    st.session_state["messages"].append({"role": "user", "content": user_prompt})
    st.chat_message("user").markdown(user_prompt)

    decision = ask_gemini_for_tool_and_server(user_prompt)
    explanation = f"💡 {decision.get('explanation', 'I’m figuring out how to help you...')}"
    st.session_state["messages"].append({"role": "assistant", "content": explanation})
    st.chat_message("assistant").markdown(explanation)

    server_name = decision.get("server")
    server_url = servers[0]["url"]
    if server_name:
        for s in servers:
            if s["name"] == server_name:
                server_url = s["url"]
                break

    tool_name = decision.get("tool")

    # Handle “show all details”
    if any(phrase in user_prompt.lower() for phrase in [
        "show me all details", "full cluster", "complete overview", "everything about cluster"
    ]):
        st.chat_message("assistant").markdown("🔍 Gathering full cluster overview...")

        cluster_context = {}
        errors = []

        # Get Nodes
        with st.spinner("📡 Fetching nodes..."):
            nodes_resp = call_tool("kubectl_get", {"resourceType": "nodes", "format": "json"}, server_url=server_url)
            if is_valid_k8s_response(nodes_resp):
                cluster_context["nodes"] = nodes_resp.get("result", {})
                if isinstance(cluster_context["nodes"], dict) and "items" in cluster_context["nodes"] and len(cluster_context["nodes"]["items"]) > 0:
                    first_node = cluster_context["nodes"]["items"][0].get("metadata", {}).get("name", "unknown-cluster")
                    cluster_name = clean_cluster_name(first_node)
                    if cluster_name:
                        cluster_context["cluster_name"] = cluster_name
                        st.session_state["last_known_cluster_name"] = cluster_name
                    st.session_state["last_known_cluster_size"] = len(cluster_context["nodes"].get("items", []))
            else:
                errors.append("Could not fetch nodes")

        # Get Namespaces
        with st.spinner("📚 Fetching namespaces..."):
            ns_resp = call_tool("kubectl_get", {"resourceType": "namespaces", "format": "json"}, server_url=server_url)
            if is_valid_k8s_response(ns_resp):
                cluster_context["namespaces"] = ns_resp.get("result", {})
            else:
                errors.append("Could not fetch namespaces")

        # Get Pods
        with st.spinner("📦 Fetching pods..."):
            pods_resp = call_tool("kubectl_get", {"resourceType": "pods", "allNamespaces": True, "format": "json"}, server_url=server_url)
            if is_valid_k8s_response(pods_resp):
                cluster_context["pods"] = pods_resp.get("result", {})
            else:
                errors.append("Could not fetch pods")

        # Get Deployments
        with st.spinner("🚀 Fetching deployments..."):
            dep_resp = call_tool("kubectl_get", {"resourceType": "deployments", "allNamespaces": True, "format": "json"}, server_url=server_url)
            if is_valid_k8s_response(dep_resp):
                cluster_context["deployments"] = dep_resp.get("result", {})
            else:
                errors.append("Could not fetch deployments")

        if cluster_context:
            final_answer = ask_gemini_answer(user_prompt, {}, context=cluster_context)
        else:
            final_answer = (
                "⚠️ I couldn't retrieve any data from your cluster.\n\n"
                "Possible reasons:\n"
                "• MCP server can't connect to Kubernetes\n"
                "• Insufficient permissions (RBAC)\n"
                "• Cluster is empty or down\n\n"
                "🛠 Please check your setup or contact your administrator."
            )

        if errors and debug_mode:
            final_answer += f"\n\n---\n🔍 *Debug: {', '.join(errors)}*"

        msg_obj = {"role": "assistant", "content": final_answer}
        if debug_mode:
            msg_obj["raw"] = cluster_context

        st.session_state["messages"].append(msg_obj)
        st.chat_message("assistant").markdown(final_answer)
        return

    # Execute tool
    if tool_name:
        tool_args = decision.get("args") or {}
        display_args = json.dumps(tool_args, indent=2, ensure_ascii=False)
        st.chat_message("assistant").markdown(
            f"🔧 Executing *{tool_name}*...\n```json\n{display_args}\n```"
        )

        resp = call_tool(tool_name, tool_args, server_url=server_url)

        # Force cluster name inference
        if "cluster name" in user_prompt.lower() and not is_valid_k8s_response(resp):
            st.chat_message("assistant").markdown("📌 Inferring cluster name from nodes...")
            node_resp = call_tool("kubectl_get", {"resourceType": "nodes", "format": "json"}, server_url=server_url)
            if is_valid_k8s_response(node_resp):
                items = node_resp.get("result", {}).get("items", [])
                if items:
                    first_node = items[0].get("metadata", {}).get("name", "unknown-cluster")
                    cluster_hint = clean_cluster_name(first_node)
                    if cluster_hint:
                        st.session_state["last_known_cluster_name"] = cluster_hint
                        resp = {"result": f"Inferred cluster name: {cluster_hint} (from node {first_node})"}
                        st.chat_message("assistant").markdown(f"✅ Cluster name: **{cluster_hint}**")

        if is_valid_k8s_response(resp):
            final_answer = ask_gemini_answer(user_prompt, resp)
        else:
            final_answer = generate_fallback_answer(user_prompt, resp)

        msg_obj = {"role": "assistant", "content": final_answer}
        if debug_mode:
            msg_obj["raw"] = resp

        st.session_state["messages"].append(msg_obj)
        st.chat_message("assistant").markdown(final_answer)

    else:
        helpful_response = (
            "🤔 I couldn't find the right tool for that. Try asking:\n\n"
            "• “Show me all details in my cluster” → Full report\n"
            "• “How many nodes?” → Node count\n"
            "• “List pods in jenkins namespace” → Specific query\n"
            "• “What’s my cluster name?” → Name inference\n\n"
            "💡 Tip: Be specific! I work best with clear questions."
        )
        st.session_state["messages"].append({"role": "assistant", "content": helpful_response})
        st.chat_message("assistant").markdown(helpful_response)


if __name__ == "__main__":
    main()
