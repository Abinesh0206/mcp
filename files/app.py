# app.py — FINAL WORKING VERSION — GEMINI + FALLBACK + PLAIN TEXT

# ================= IMPORTS =================
import os
import json
import time
import requests
import streamlit as st
from dotenv import load_dotenv
from typing import Optional, Dict, Any
import google.generativeai as genai


# ================= CONFIG =================
load_dotenv()

# ✅ USE gemini-2.0-flash-lite → 1,000 free requests/day
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyD_ZoULiDzQO_ws6GrNvclHyuGbAL1nkIc")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash-lite")

GEMINI_AVAILABLE = False
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model_list = [m.name for m in genai.list_models()]
        if f"models/{GEMINI_MODEL}" not in model_list:
            st.warning(f"⚠️ Model {GEMINI_MODEL} not available. Using fallback routing.")
        else:
            GEMINI_AVAILABLE = True
    except Exception as e:
        st.error(f"❌ Gemini setup error: {e}")


# ================= SERVER MANAGEMENT =================
def load_servers() -> list:
    try:
        with open("servers.json") as f:
            data = json.load(f)
            return data.get("servers", []) or []
    except Exception:
        return [{"name": "default", "url": "http://127.0.0.1:3000/mcp", "description": "Fallback server"}]

servers = load_servers() or [{"name": "default", "url": "http://127.0.0.1:3000/mcp", "description": "Fallback server"}]


# ================= HELPERS =================
def call_mcp_server(method: str, params: Optional[Dict[str, Any]] = None, server_url: Optional[str] = None, timeout: int = 20) -> Dict[str, Any]:
    url = server_url or servers[0]["url"]
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream, */*"}

    try:
        res = requests.post(url, json=payload, headers=headers, timeout=timeout)
        res.raise_for_status()
        text = res.text.strip()

        if "data:" in text:  # handle SSE-like responses
            for line in text.splitlines():
                if line.startswith("data:"):
                    try:
                        return json.loads(line[5:].strip())
                    except Exception:
                        return {"result": line[5:].strip()}

        try:
            return res.json()
        except ValueError:
            return {"result": res.text}

    except requests.exceptions.RequestException as e:
        return {"error": f"MCP server request failed: {str(e)}"}


def list_mcp_tools(server_url: Optional[str] = None) -> list:
    resp = call_mcp_server("tools/list", server_url=server_url)
    if isinstance(resp, dict):
        result = resp.get("result")
        if isinstance(result, dict):
            return result.get("tools", [])
        if isinstance(result, list):
            return result
    return []


def call_tool(name: str, arguments: dict, server_url: Optional[str] = None) -> Dict[str, Any]:
    return call_mcp_server("tools/call", {"name": name, "arguments": arguments or {}}, server_url=server_url)


user_prompt_global = ""


def sanitize_args(args: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not args:
        return {}
    fixed = dict(args)

    if "resource" in fixed and "resourceType" not in fixed:
        fixed["resourceType"] = fixed.pop("resource")

    user_keywords_for_all = ["all", "everything", "show me all", "entire cluster", "across all", "all namespaces"]
    namespace_val = str(fixed.get("namespace", "")).lower()

    if any(kw in user_prompt_global.lower() for kw in user_keywords_for_all) or \
       namespace_val in ["all", "all-namespaces", "everything", "*"]:
        fixed["allNamespaces"] = True
        fixed.pop("namespace", None)

    if fixed.get("resourceType") == "pods" and "namespace" not in fixed and not fixed.get("allNamespaces"):
        fixed["namespace"] = "default"

    return fixed


# ================= GEMINI TOOL ROUTER (WITH FALLBACK) =================
def ask_gemini_for_tool_and_server(query: str, retries: int = 2) -> Dict[str, Any]:
    query_lower = query.lower()

    # Hardcoded rules
    if "pod" in query_lower and ("all" in query_lower or "list" in query_lower or "show" in query_lower):
        return {"tool": "kubectl_get", "args": {"resourceType": "pods", "allNamespaces": True}, "server": "default", "explanation": "Listing pods"}
    if "namespace" in query_lower and ("all" in query_lower or "list" in query_lower):
        return {"tool": "kubectl_get", "args": {"resourceType": "namespaces"}, "server": "default", "explanation": "Listing namespaces"}
    if "service" in query_lower and ("all" in query_lower or "list" in query_lower):
        return {"tool": "kubectl_get", "args": {"resourceType": "services", "allNamespaces": True}, "server": "default", "explanation": "Listing services"}
    if "deployment" in query_lower and ("all" in query_lower or "list" in query_lower):
        return {"tool": "kubectl_get", "args": {"resourceType": "deployments", "allNamespaces": True}, "server": "default", "explanation": "Listing deployments"}

    if not GEMINI_AVAILABLE:
        return {"tool": None, "args": None, "server": None, "explanation": "⚠️ Gemini unavailable. Try simple queries."}

    tool_names = [t.get("name") for s in servers for t in list_mcp_tools(s["url"]) if isinstance(t, dict)]
    server_names = [s["name"] for s in servers]

    instruction = f"""
You are an AI router. Map user query to ONE MCP tool and ONE server.

User: "{query}"
Servers: {json.dumps(server_names)}
Tools: {json.dumps(tool_names)}

Return strict JSON: 
{{"tool": "<tool_name>", "args": {{"resourceType": "..."}}, "server": "<server_name>", "explanation": "short"}}
"""

    for attempt in range(retries):
        try:
            model = genai.GenerativeModel(GEMINI_MODEL)
            resp = model.generate_content(instruction, generation_config={"temperature": 0.0})
            text = getattr(resp, "text", str(resp)).strip()

            if "```json" in text:
                start = text.find("```json") + 7
                end = text.rfind("```")
                text = text[start:end] if end > start else text

            parsed = None
            if "{" in text:
                try:
                    parsed = json.loads(text)
                except Exception:
                    import re
                    m = re.search(r'\{.*\}', text, re.DOTALL)
                    if m:
                        parsed = json.loads(m.group())

            if isinstance(parsed, dict):
                parsed["args"] = sanitize_args(parsed.get("args") or {})
                if parsed.get("tool"):
                    return parsed

        except Exception:
            continue

    return {"tool": None, "args": None, "server": None, "explanation": "⚠️ Gemini failed."}


# ================= OUTPUT FORMATTER (NO HTML) =================
def ask_gemini_answer(user_input: str, raw_response: dict) -> str:
    try:
        result = raw_response.get("result", raw_response) if isinstance(raw_response, dict) else raw_response
        if isinstance(result, list):
            lines = []
            for item in result:
                if isinstance(item, dict):
                    name = item.get("name", "Unnamed")
                    ns = item.get("namespace", "default")
                    status = item.get("status", "Unknown")
                    lines.append(f"- {name} (ns: {ns}, status: {status})")
                else:
                    lines.append(f"- {item}")
            return "\n".join(lines)
        elif isinstance(result, str):
            return result
        return json.dumps(result, indent=2)
    except Exception:
        return str(raw_response)


def ask_gemini_prettify(user_input: str, response: Any, max_tokens: int = 512) -> str:
    if not GEMINI_AVAILABLE:
        return ask_gemini_answer(user_input, response)

    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        prompt = (
            f"User asked: {user_input}\n\n"
            "Here is the raw MCP/cluster response:\n"
            f"{json.dumps(response, indent=2, default=str)}\n\n"
            "Convert it into clear, plain text for an admin. Use simple bullets or sentences. No JSON, no HTML, no markdown."
        )
        resp = model.generate_content(prompt, generation_config={"temperature": 0.0, "max_output_tokens": max_tokens})
        return getattr(resp, "text", str(resp)).strip()
    except Exception:
        return ask_gemini_answer(user_input, response)


# ================= CLUSTER SUMMARY =================
RESOURCE_TYPES = ["pods", "services", "deployments", "jobs", "cronjobs", "configmaps", "secrets", "ingresses", "namespaces", "nodes", "pv", "pvc"]

def get_cluster_summary(server_url: str) -> dict:
    summary = {}
    for r in RESOURCE_TYPES:
        resp = call_tool("kubectl_get", {"resourceType": r, "allNamespaces": True}, server_url)
        summary[r] = resp.get("result", resp) if isinstance(resp, dict) else resp
    return summary


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

    global user_prompt_global
    user_prompt_global = user_prompt

    st.session_state["messages"].append({"role": "user", "content": user_prompt})
    st.chat_message("user").markdown(user_prompt)

    if "all resources" in user_prompt.lower():
        st.chat_message("assistant").markdown("💡 Fetching full cluster state...")
        summary = get_cluster_summary(servers[0]["url"])
        final_answer = ask_gemini_prettify(user_prompt, summary)
        st.session_state["messages"].append({"role": "assistant", "content": final_answer})
        st.chat_message("assistant").markdown(final_answer)
        return

    decision = ask_gemini_for_tool_and_server(user_prompt)
    st.chat_message("assistant").markdown(decision.get("explanation", "Decision made."))

    if not decision.get("tool"):
        answer = "⚠️ Could not determine tool. Try: 'show me all pods', 'list namespaces'."
        st.session_state["messages"].append({"role": "assistant", "content": answer})
        st.chat_message("assistant").markdown(answer)
        return

    tool_name = decision["tool"]
    tool_args = decision.get("args") or {}
    server_url = next((s["url"] for s in servers if s["name"] == decision["server"]), servers[0]["url"])

    st.chat_message("assistant").markdown(f"🔧 Running {tool_name} with {json.dumps(tool_args)}")
    resp = call_tool(tool_name, tool_args, server_url=server_url)

    if not resp or "error" in resp:
        final_answer = f"⚠️ Execution failed: {resp.get('error', 'Unknown error') if isinstance(resp, dict) else str(resp)}"
    else:
        final_answer = ask_gemini_prettify(user_prompt, resp)

    st.session_state["messages"].append({"role": "assistant", "content": final_answer})
    st.chat_message("assistant").markdown(final_answer)


if __name__ == "__main__":
    main()
