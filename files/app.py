# app.py — FINAL WORKING VERSION — GEMINI + FALLBACK + NATURAL OUTPUT

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

# ✅ USE gemini-2.0-flash-lite → 1,000 free requests/day (NOT 1.5-flash)
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

        if "data:" in text:
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
    """Try Gemini first, fallback to hardcoded rules if it fails."""

    # ✅ HARDCODED FALLBACK RULES — NO GEMINI NEEDED
    query_lower = query.lower()

    if "pod" in query_lower and ("all" in query_lower or "list" in query_lower or "show" in query_lower):
        return {
            "tool": "kubectl_get",
            "args": {"resourceType": "pods", "allNamespaces": True},
            "server": "default",
            "explanation": "💡 Fallback: Detected 'pods' request → using kubectl_get."
        }

    if "namespace" in query_lower and ("all" in query_lower or "list" in query_lower):
        return {
            "tool": "kubectl_get",
            "args": {"resourceType": "namespaces"},
            "server": "default",
            "explanation": "💡 Fallback: Detected 'namespaces' request → using kubectl_get."
        }

    if "service" in query_lower and ("all" in query_lower or "list" in query_lower):
        return {
            "tool": "kubectl_get",
            "args": {"resourceType": "services", "allNamespaces": True},
            "server": "default",
            "explanation": "💡 Fallback: Detected 'services' request → using kubectl_get."
        }

    if "deployment" in query_lower and ("all" in query_lower or "list" in query_lower):
        return {
            "tool": "kubectl_get",
            "args": {"resourceType": "deployments", "allNamespaces": True},
            "server": "default",
            "explanation": "💡 Fallback: Detected 'deployments' request → using kubectl_get."
        }

    # ✅ ONLY USE GEMINI IF FALLBACK DOESN’T MATCH
    if not GEMINI_AVAILABLE:
        return {
            "tool": None,
            "args": None,
            "server": None,
            "explanation": "⚠️ Gemini unavailable. Try: 'show me all pods', 'list namespaces', etc."
        }

    tool_names = [t.get("name") for s in servers for t in list_mcp_tools(s["url"])]
    server_names = [s["name"] for s in servers]

    instruction = f"""
You are an AI router. Map user query to ONE MCP tool and ONE server.

User: "{query}"
Servers: {json.dumps(server_names)}
Tools: {json.dumps(tool_names)}

Return STRICT JSON:
{{"tool": "<tool_name>", "args": {{"resourceType": "...", "allNamespaces": true}}, "server": "<server_name>", "explanation": "short"}}

Example for "show me all pods":
{{"tool": "kubectl_get", "args": {{"resourceType": "pods", "allNamespaces": true}}, "server": "default", "explanation": "Fetching all pods"}}

Do NOT answer the question. Only return JSON.
"""

    for attempt in range(retries):
        try:
            model = genai.GenerativeModel(GEMINI_MODEL)
            resp = model.generate_content(instruction, generation_config={"temperature": 0.0})
            text = getattr(resp, "text", str(resp)).strip()

            # Extract JSON if wrapped in ```json or markdown
            if "```json" in text:
                start = text.find("```json") + 7
                end = text.rfind("```")
                text = text[start:end] if end > start else text

            parsed = json.loads(text) if "{" in text else None
            if not parsed:
                import re
                json_match = re.search(r'\{.*\}', text, re.DOTALL)
                if json_match:
                    parsed = json.loads(json_match.group())

            if isinstance(parsed, dict):
                parsed["args"] = sanitize_args(parsed.get("args") or {})
                if parsed.get("tool"):
                    return parsed

        except Exception as e:
            if attempt == retries - 1:
                st.warning(f"⚠️ Gemini routing failed: {e}")

    # Final fallback
    return {
        "tool": None,
        "args": None,
        "server": None,
        "explanation": "⚠️ Gemini failed. Try simple commands like 'show me all pods'."
    }


# ================= OUTPUT FORMATTER =================
def ask_gemini_answer(user_input: str, raw_response: dict) -> str:
    """Convert raw response into natural, human-friendly bullet points."""

    # For "all resources" — summarize
    if "all resources" in user_input.lower():
        try:
            clean_summary = {}
            raw_summary = raw_response

            for rtype, data in raw_summary.items():
                items = []
                result = data.get("result", data) if isinstance(data, dict) else data
                if isinstance(result, list):
                    for item in result:
                        if isinstance(item, dict):
                            name = item.get("name", "Unnamed")
                            namespace = item.get("namespace", "default")
                            status = item.get("status", "Unknown")
                            items.append(f"{name} ({namespace}, {status})")
                clean_summary[rtype] = items

            if GEMINI_AVAILABLE:
                model = genai.GenerativeModel(GEMINI_MODEL)
                prompt = (
                    f"User asked: {user_input}\n\n"
                    f"Cluster contains these resources:\n{json.dumps(clean_summary, indent=2)}\n\n"
                    "Summarize this in clear, natural English bullet points. Group by resource type. "
                    "Example: • Pods: 5 running in argocd, 20 succeeded in default\n"
                    "DO NOT show raw JSON. Keep it concise."
                )
                resp = model.generate_content(prompt)
                answer = getattr(resp, "text", str(resp)).strip()

                lines = [line.strip() for line in answer.splitlines() if line.strip()]
                html_list = "<ul style='margin: 0; padding-left: 1.5rem;'>" + \
                           "".join([f"<li style='margin-bottom: 0.3rem;'>• {line}</li>" for line in lines]) + \
                           "</ul>"
                return html_list

            else:
                parts = []
                for rtype, items in clean_summary.items():
                    count = len(items) if isinstance(items, list) else 0
                    parts.append(f"• {rtype.capitalize()}: {count} items")
                return "<ul>" + "".join([f"<li>{p}</li>" for p in parts]) + "</ul>"

        except Exception as e:
            return f"<p>⚠️ Summarization failed: {str(e)}</p>"

    # Normal case — format as clean bullets
    try:
        items = []
        result = raw_response.get("result", raw_response) if isinstance(raw_response, dict) else raw_response

        if isinstance(result, list):
            for item in result:
                if isinstance(item, dict):
                    name = item.get("name", "Unnamed")
                    namespace = item.get("namespace", "default")
                    status = item.get("status", "Unknown")
                    items.append(f"• {name} ({namespace}, {status})")
                else:
                    items.append(f"• {item}")
        elif isinstance(result, str):
            lines = [line.strip() for line in result.splitlines() if line.strip()]
            items = [f"• {line}" for line in lines]
        else:
            items.append(f"• {str(result)}")

        if items:
            html_list = "<ul style='margin: 0; padding-left: 1.5rem; line-height: 1.4;'>" + \
                       "".join([f"<li>{item}</li>" for item in items]) + \
                       "</ul>"
            return html_list

    except Exception as e:
        pass

    return f"<pre style='background:#f4f4f4; padding:10px; border-radius:5px;'>{json.dumps(raw_response, indent=2)}</pre>"


# ================= CLUSTER SUMMARY =================
RESOURCE_TYPES = [
    "pods",
    "services",
    "deployments",
    "jobs",
    "cronjobs",
    "configmaps",
    "secrets",
    "ingresses",
    "namespaces",
    "nodes",
    "pv",
    "pvc"
]

def get_cluster_summary(server_url: str) -> dict:
    summary = {}
    for r in RESOURCE_TYPES:
        resp = call_tool("kubectl_get", {"resourceType": r, "allNamespaces": True}, server_url)
        if isinstance(resp, dict) and "result" in resp:
            summary[r] = resp["result"]
        else:
            summary[r] = resp
    return summary


# ================= STREAMLIT APP =================
def main():
    st.set_page_config(page_title="MCP Chat Assistant", page_icon="⚡", layout="wide")
    st.title("🤖 Masa Bot Assistant")

    if "messages" not in st.session_state:
        st.session_state["messages"] = []

    for msg in st.session_state["messages"]:
        with st.chat_message(msg.get("role", "assistant")):
            content = msg.get("content", "")
            if isinstance(content, str) and ("<ul>" in content or "<li>" in content or "<pre>" in content):
                st.markdown(content, unsafe_allow_html=True)
            else:
                st.markdown(content)

    user_prompt = st.chat_input("Ask Kubernetes or ArgoCD something...")
    if not user_prompt:
        return

    global user_prompt_global
    user_prompt_global = user_prompt

    st.session_state["messages"].append({"role": "user", "content": user_prompt})
    st.chat_message("user").markdown(user_prompt)

    # Handle "all resources"
    if "all resources" in user_prompt.lower():
        explanation = "💡 Fetching and summarizing full cluster state (all namespaces, all types)."
        st.session_state["messages"].append({"role": "assistant", "content": explanation})
        st.chat_message("assistant").markdown(explanation)

        server_url = servers[0]["url"]
        summary = get_cluster_summary(server_url)
        final_answer = ask_gemini_answer(user_prompt, summary)

        st.session_state["messages"].append({"role": "assistant", "content": final_answer})
        with st.chat_message("assistant"):
            st.markdown(final_answer, unsafe_allow_html=True)
        return

    # ✅ TOOL SELECTION — GEMINI OR FALLBACK
    decision = ask_gemini_for_tool_and_server(user_prompt)
    explanation = decision.get("explanation", "Tool decision produced.")
    st.session_state["messages"].append({"role": "assistant", "content": explanation})
    st.chat_message("assistant").markdown(explanation)

    if not decision.get("tool"):
        answer = "⚠️ Could not determine tool. Try: 'show me all pods', 'list namespaces', 'get all services'."
        st.session_state["messages"].append({"role": "assistant", "content": answer})
        st.chat_message("assistant").markdown(answer)
        return

    # Execute tool
    server_url = next((s["url"] for s in servers if s["name"] == decision.get("server")), servers[0]["url"])
    tool_name = decision.get("tool")
    tool_args = decision.get("args") or {}

    st.chat_message("assistant").markdown(
        f"🔧 Executing *{tool_name}* on server {decision.get('server')} with arguments:\n```json\n{json.dumps(tool_args, indent=2)}\n```"
    )

    resp = call_tool(tool_name, tool_args, server_url=server_url)

    if not resp or "error" in resp:
        final_answer = f"⚠️ Execution failed: {resp.get('error', 'Unknown error') if isinstance(resp, dict) else str(resp)}"
    else:
        final_answer = ask_gemini_answer(user_prompt, resp)

    st.session_state["messages"].append({"role": "assistant", "content": final_answer})
    with st.chat_message("assistant"):
        st.markdown(final_answer, unsafe_allow_html=True)


if __name__ == "__main__":
    main()
