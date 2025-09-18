# app.py — IMPROVED VERSION (Client-side logic enhanced)

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

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyAkqKd3Hc60Qf6N_3ZYj1eu_GtFzkMmMVQ")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash-lite")

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

    # Normalize resourceType
    if "resource" in fixed and "resourceType" not in fixed:
        fixed["resourceType"] = fixed.pop("resource")

    # ✅ IMPROVED: If user says "all", "all pods", "everything", "show me all", etc. → force allNamespaces=True
    user_keywords_for_all = ["all", "everything", "show me all", "entire cluster", "across all", "all namespaces"]
    namespace_val = str(fixed.get("namespace", "")).lower()
    if any(kw in user_prompt_global.lower() for kw in user_keywords_for_all) or \
       namespace_val in ["all", "all-namespaces", "allnamespace", "everything", "*"]:
        fixed["allNamespaces"] = True
        fixed.pop("namespace", None)

    # ✅ Default to "default" namespace ONLY if allNamespaces is NOT set
    if fixed.get("resourceType") == "pods" and "namespace" not in fixed and not fixed.get("allNamespaces"):
        fixed["namespace"] = "default"

    return fixed


# 🌍 GLOBAL VARIABLE TO HOLD USER PROMPT (for sanitize_args logic above)
user_prompt_global = ""


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

    instruction = f"""
You are an AI router. Your task is to map a user query to ONE MCP tool and ONE MCP server.

User query: "{query}"

Available servers: {json.dumps(server_names)}
Available tools: {json.dumps(tool_names)}

Return STRICT JSON only:
{{"tool": "<tool_name_or_null>", "args": {{ ... }}, "server": "<server_name_or_null>", "explanation": "short explanation"}}
If unsure, set tool and server to null.
Do NOT answer the user question here. Only map it.
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
    """Convert raw MCP response into clean bullet points."""
    if not GEMINI_AVAILABLE:
        # 🚫 FALLBACK: Format as bullet points manually if Gemini fails
        try:
            items = []
            if isinstance(raw_response, dict) and "result" in raw_response:
                result = raw_response["result"]
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
                    # Try to split lines or comma-separated
                    lines = result.splitlines()
                    for line in lines:
                        if line.strip():
                            items.append(f"• {line.strip()}")
                else:
                    items.append(f"• {str(result)}")
            else:
                items.append(f"• {str(raw_response)}")

            return "\n".join(items) if items else "No data returned."
        except Exception as e:
            return f"⚠️ Fallback formatting failed: {str(e)}"

    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        prompt = (
            f"User asked: {user_input}\n\n"
            f"Raw MCP response:\n{json.dumps(raw_response, indent=2)}\n\n"
            "Rewrite the response as clear, concise bullet points (•). "
            "DO NOT use paragraphs. DO NOT add explanations. "
            "Format each item as: • <name> (<namespace>, <status>) if applicable. "
            "If it's a list of strings, just prefix each with •. "
            "Keep it clean and machine-readable."
        )
        resp = model.generate_content(prompt)
        answer = getattr(resp, "text", str(resp)).strip()

        # ✅ EXTRA SAFETY: If Gemini returns paragraph, force bullet it
        if "\n•" not in answer and not answer.startswith("•"):
            lines = [line.strip() for line in answer.splitlines() if line.strip()]
            bulleted = "\n".join(f"• {line}" for line in lines)
            return bulleted if bulleted else answer

        return answer

    except Exception as e:
        return f"Gemini error while post-processing: {str(e)}"


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
    """Collects all resource types for full cluster summary."""
    summary = {}
    for r in RESOURCE_TYPES:
        resp = call_tool("kubectl_get", {"resourceType": r, "allNamespaces": True}, server_url)
        summary[r] = resp.get("result") if isinstance(resp, dict) else resp
    return summary


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

    # 🌍 SET GLOBAL USER PROMPT (used in sanitize_args)
    global user_prompt_global
    user_prompt_global = user_prompt

    # Store user message
    st.session_state["messages"].append({"role": "user", "content": user_prompt})
    st.chat_message("user").markdown(user_prompt)

    # Special case: full cluster summary
    if "all resources" in user_prompt.lower():
        explanation = "💡 Fetching full cluster summary (all namespaces, all resource types)."
        st.session_state["messages"].append({"role": "assistant", "content": explanation})
        st.chat_message("assistant").markdown(explanation)

        server_url = servers[0]["url"]
        summary = get_cluster_summary(server_url)
        final_answer = ask_gemini_answer(user_prompt, summary)

        st.session_state["messages"].append({"role": "assistant", "content": final_answer})
        st.chat_message("assistant").markdown(final_answer)
        return

    # Ask Gemini for routing
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
