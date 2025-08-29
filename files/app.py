# streamlit_masabot_mcp.py
import os
import json
import re
import requests
import streamlit as st
from typing import Any, Dict, Optional

# ---------- Config ----------
OLLAMA_BASE = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
TITLE = os.getenv("UI_TITLE", "MasaBot")
PRIMARY = os.getenv("THEME_PRIMARY", "#1e88e5")
ACCENT = os.getenv("THEME_ACCENT", "#ff6f00")
CONFIG_PATH = os.path.join(os.getcwd(), "mcp_config.json")

with open(CONFIG_PATH, "r") as f:
    MCP_CFG = json.load(f)

# ---------- Utilities ----------
def safe_json_load(s: str) -> Optional[Any]:
    try:
        return json.loads(s)
    except Exception:
        return None

def expand_auth_header(header: str) -> str:
    return re.sub(r"\$\{([^}]+)\}", lambda m: os.getenv(m.group(1), ""), header)

# ---------- MCP HTTP Caller (returns parsed JSON when possible) ----------
def call_mcp_http_raw(server: Dict, query: str, timeout=60) -> Any:
    """
    Call MCP server. Return Python object (dict/list) if response JSON, else raw string.
    """
    base = server["baseUrl"].rstrip("/")
    headers = {}
    if server.get("authHeader"):
        headers["Authorization"] = expand_auth_header(server["authHeader"])
    try:
        resp = requests.post(f"{base}/query", json={"prompt": query}, headers=headers, timeout=timeout)
        if resp.status_code == 404:
            resp = requests.post(f"{base}/chat", json={"prompt": query}, headers=headers, timeout=timeout)
        resp.raise_for_status()
        # Try parse JSON
        try:
            return resp.json()
        except Exception:
            return resp.text
    except Exception as e:
        return {"error": f"[MCP:{server['name']}] {e}"}

# ---------- MCP Result Postprocessing Helpers ----------
def filter_pods_list(pods: list) -> list:
    """
    Accepts list of pod strings or dicts and filters out CrashLoopBackOff | Evicted
    """
    filtered = []
    for p in pods:
        text = p
        # if dict, try extract name/state keys
        if isinstance(p, dict):
            # try common keys
            name = p.get("name") or p.get("pod") or ""
            state = p.get("state") or p.get("status") or ""
            text = f"{name} {state}"
        # filter known bad statuses
        low = text.lower()
        if "crashloop" in low or "evicted" in low or "completed" in low and "completed" not in low:
            continue
        filtered.append(p)
    return filtered

def pretty_mcp_response(mapped_query: str, raw_resp: Any) -> str:
    """
    Convert raw MCP response to a friendly string, and compute counts where required.
    """
    # If error object returned
    if isinstance(raw_resp, dict) and raw_resp.get("error"):
        return raw_resp["error"]

    # Normalize string results that are JSON-like
    if isinstance(raw_resp, str):
        maybe = safe_json_load(raw_resp)
        if maybe is not None:
            raw_resp = maybe

    # If MCP returned dict with namespaces/pods fields, use them
    if isinstance(raw_resp, dict):
        # Namespaces case
        if "namespaces" in raw_resp:
            ns = raw_resp.get("namespaces") or []
            ns = [n for n in ns if isinstance(n, str)]
            return f"Namespaces ({len(ns)}): {', '.join(ns)}"
        # Pods case
        if "pods" in raw_resp:
            pods = raw_resp.get("pods") or []
            pods = filter_pods_list(pods)
            return f"Pods ({len(pods)}): " + ", ".join([p if isinstance(p, str) else p.get("name", str(p)) for p in pods])
        # Generic - try pretty print
        return json.dumps(raw_resp, indent=2)

    # If MCP returned a list (likely pods)
    if isinstance(raw_resp, list):
        pods = filter_pods_list(raw_resp)
        return f"Items ({len(pods)}): " + ", ".join([str(p) for p in pods])

    # Fallback: raw text
    return str(raw_resp)

# ---------- Ollama Caller with tightened system prompt ----------
def call_ollama(user_text: str, model: str = "mistral:7b-instruct-v0.2-q4_0") -> str:
    """
    Calls Ollama with a strict system prompt. Ollama MUST return either:
      - strict JSON: {"target":"kubernetes","query":"..."} OR
      - plain natural language answer.
    """
    system_prompt = f"""
You are MasaBot, a DevOps AI assistant.

If the user is asking for a live/system action (Kubernetes, ArgoCD, Jenkins), respond ONLY with strict JSON (no surrounding text):
{{"target":"<kubernetes|argocd|jenkins>", "query":"<mcp-mapped-command>"}}

Allowed targets = ["kubernetes", "argocd", "jenkins"]

MCP-compatible mappings (examples) - the assistant must map natural intents to these exact command tokens:
- Show all namespaces -> "get namespaces"
- How many namespaces -> "get namespaces" (the client will count)
- Show all pods -> "list-pods"
- Show pods in NAMESPACE -> "list-pods -n NAMESPACE"
- How many pods -> "list-pods" (the client will count, optionally with -n NAMESPACE)
- Show all services -> "list-services"
- Show deployments -> "list-deployments"
- Create namespace NAME -> "create-namespace NAME"
- Delete namespace NAME -> "delete-namespace NAME"
- Create secret NAME in NAMESPACE -> "create-secret NAME -n NAMESPACE"
- Sync app APPNAME (argocd) -> "sync app APPNAME"
- List apps (argocd) -> "list apps"
- List all jobs (jenkins) -> "list all jobs"
- Build job JOBNAME (jenkins) -> "build JOBNAME"

Important rules:
- Do NOT invent commands. Use the exact tokens above.
- Do NOT return natural language if this is a system query — ONLY return the strict JSON.
- If unsure whether this is a system query, prefer natural language explanation.

User: {user_text}
Assistant:"""

    payload = {"model": model, "prompt": system_prompt, "stream": False}
    try:
        r = requests.post(f"{OLLAMA_BASE}/api/generate", json=payload, timeout=120)
        r.raise_for_status()
        js = r.json()
        # Ollama SDK may return a 'response' field or nested structure
        if isinstance(js, dict):
            if "response" in js:
                return str(js["response"]).strip()
            # some Ollama builds return choices/text
            if "choices" in js and isinstance(js["choices"], list) and js["choices"]:
                txt = js["choices"][0].get("message", {}).get("content") or js["choices"][0].get("text")
                return (txt or "").strip()
        return str(js)
    except Exception as e:
        return f"[Ollama] error: {e}"

# ---------- Intent normalization (fallback) ----------
def fallback_intent_parser(text: str) -> Optional[Dict]:
    """
    If Ollama fails to give strict JSON, attempt to parse common intents here and return a dict with target+query.
    """
    t = text.lower().strip()

    # Namespaces
    if re.search(r"\bhow many namespaces\b", t):
        return {"target": "kubernetes", "query": "get namespaces"}
    if re.search(r"\b(show|list).*(namespaces|all namespaces)\b", t):
        return {"target": "kubernetes", "query": "get namespaces"}

    # Pods count / list with optional namespace
    m = re.search(r"(how many|number of)\s+pods(?:\s+in\s+([a-z0-9-]+))?", t)
    if m:
        ns = m.group(2)
        if ns:
            return {"target": "kubernetes", "query": f"list-pods -n {ns}"}
        return {"target": "kubernetes", "query": "list-pods"}

    m = re.search(r"show(?: | me)? (all )?pods(?: in ([a-z0-9-]+))?", t)
    if m:
        ns = m.group(2)
        if ns:
            return {"target": "kubernetes", "query": f"list-pods -n {ns}"}
        return {"target": "kubernetes", "query": "list-pods"}

    # Delete/Create namespace
    m = re.search(r"delete (?:the )?namespace\s+([a-z0-9-]+)", t)
    if m:
        name = m.group(1)
        return {"target": "kubernetes", "query": f"delete-namespace {name}"}
    m = re.search(r"create (?:a )?namespace\s+([a-z0-9-]+)", t)
    if m:
        name = m.group(1)
        return {"target": "kubernetes", "query": f"create-namespace {name}"}

    # ArgoCD / Jenkins basic
    if "sync app" in t and "argocd" in t or "sync app" in t:
        m = re.search(r"sync app\s+([^\s]+)", t)
        if m:
            return {"target": "argocd", "query": f"sync app {m.group(1)}"}
    if "list apps" in t:
        return {"target": "argocd", "query": "list apps"}

    if "list all jobs" in t:
        return {"target": "jenkins", "query": "list all jobs"}
    m = re.search(r"build job\s+([^\s]+)", t)
    if m:
        return {"target": "jenkins", "query": f"build {m.group(1)}"}

    return None

# ---------- Server helper ----------
def get_server_by_name(name: str) -> Optional[Dict]:
    aliases = {"k8s": "kubernetes", "kube": "kubernetes", "argo": "argocd", "cd": "argocd", "jenk": "jenkins"}
    name = aliases.get(name.lower(), name.lower())
    for srv in MCP_CFG.get("servers", []):
        if srv["name"].lower() == name:
            return srv
    return None

# ---------- Streamlit UI ----------
st.set_page_config(page_title=TITLE, page_icon="🤖", layout="wide")

st.markdown(f"""
<style>
  .chat-bubble-user {{
    border-left: 4px solid {PRIMARY}; padding: 12px; margin: 8px 0;
    border-radius: 12px; background: #f5f9ff;
    font-size: 18px; line-height: 1.5;
  }}
  .chat-bubble-bot {{
    border-left: 4px solid {ACCENT}; padding: 12px; margin: 8px 0;
    border-radius: 12px; background: #fff8f0;
    font-size: 18px; line-height: 1.5;
  }}
</style>
""", unsafe_allow_html=True)

if "sessions" not in st.session_state:
    st.session_state.sessions = []
if "current" not in st.session_state:
    st.session_state.current = {"title": "New chat", "messages": []}

st.markdown("### Start chatting")
user_text = st.chat_input("Type your message…")
msgs = st.session_state.current["messages"]

# render chat history
for m in msgs:
    if m["role"] == "user":
        st.markdown(f"<div class='chat-bubble-user'>{m['content']}</div>", unsafe_allow_html=True)
    else:
        st.markdown(f"<div class='chat-bubble-bot'>{m['content']}</div>", unsafe_allow_html=True)

if user_text:
    msgs.append({"role": "user", "content": user_text})
    st.markdown(f"<div class='chat-bubble-user'>{user_text}</div>", unsafe_allow_html=True)

    # 1) Ask Ollama for mapping
    with st.spinner("Ollama thinking…"):
        ollama_answer = call_ollama(user_text)

    parsed_json = safe_json_load(ollama_answer)

    intent = None
    if isinstance(parsed_json, dict) and parsed_json.get("target") and parsed_json.get("query"):
        intent = parsed_json
    else:
        # fallback parser if Ollama didn't return valid JSON
        intent = fallback_intent_parser(user_text)

    if not intent:
        # Ollama didn't return JSON and fallback failed -> show Ollama text or a helpful msg
        answer = ollama_answer
    else:
        # We have a mapping to a target + query
        target = intent["target"]
        raw_query = intent["query"].strip()

        # Translate pseudo-commands to actual MCP commands where needed
        mapped_query = raw_query
        # If query asked for count-namespaces or count-pods, we force a get/list and count locally
        if raw_query == "count-namespaces":
            mapped_query = "get namespaces"
        if raw_query == "count-pods":
            mapped_query = "list-pods"

        server = get_server_by_name(target)
        if not server:
            answer = f"[Error] No MCP server found for target: {target}"
        else:
            with st.spinner(f"Querying MCP: {target} …"):
                raw_mcp_resp = call_mcp_http_raw(server, mapped_query)

            # If user asked specifically to count, handle here
            if raw_query in ("count-namespaces", "how many namespaces", "how many namespaces in cluster"):
                # try to extract namespaces list
                if isinstance(raw_mcp_resp, dict) and "namespaces" in raw_mcp_resp:
                    ns = raw_mcp_resp.get("namespaces") or []
                    answer = f"Namespaces count: {len(ns)}"
                else:
                    # try parse string
                    txt = raw_mcp_resp if isinstance(raw_mcp_resp, str) else json.dumps(raw_mcp_resp)
                    maybe = safe_json_load(txt)
                    if isinstance(maybe, dict) and "namespaces" in maybe:
                        ns = maybe["namespaces"]
                        answer = f"Namespaces count: {len(ns)}"
                    else:
                        # fallback: present whatever MCP returned
                        answer = pretty_mcp_response(mapped_query, raw_mcp_resp)
            else:
                # General case: pretty-print and filter pods if needed
                # If the mapped_query is list-pods (optionally with -n), filter bad pods
                if mapped_query.startswith("list-pods"):
                    # If raw_mcp_resp is dict with pods key or a list, handle appropriately
                    answer = pretty_mcp_response(mapped_query, raw_mcp_resp)
                else:
                    # For commands like delete-namespace, create-namespace, get namespaces, list-services, etc.
                    answer = pretty_mcp_response(mapped_query, raw_mcp_resp)

    # Append assistant reply and render
    msgs.append({"role": "assistant", "content": answer})
    st.markdown(f"<div class='chat-bubble-bot'>{answer}</div>", unsafe_allow_html=True)

if not st.session_state.current.get("title") and msgs:
    st.session_state.current["title"] = (msgs[0]["content"][:30] + "…") if msgs else "New chat"
