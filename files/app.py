import os
import json
import requests
import streamlit as st
from dotenv import load_dotenv
import google.generativeai as genai

# ============================== CONFIG ==============================
load_dotenv()

MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://18.234.91.216:3000/mcp")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
MCP_BEARER = os.getenv("K8S_MCP_TOKEN", "")

# Model init (used for general chat + mapping)
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(GEMINI_MODEL)
else:
    model = None

# ============================== THEME ===============================
# Make the UI feel like ChatGPT dark
st.set_page_config(page_title="MASA Bot – MCP + Gemini", page_icon="☁", layout="wide")

CHATGPT_DARK = """
<style>
/* Page background + fonts */
.stApp {
  background: #0b0f19;
  color: #e5e7eb;
  font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Helvetica Neue, Arial, "Apple Color Emoji", "Segoe UI Emoji";
}

/* Sidebar */
section[data-testid="stSidebar"] {
  background: #0f172a;
  border-right: 1px solid rgba(148,163,184,0.15);
}

/* Input box */
div[data-testid="stChatInput"] > div {
  border-radius: 14px !important;
  background: #111827 !important;
  border: 1px solid rgba(148,163,184,0.18) !important;
}

/* Chat bubbles */
[data-testid="stChatMessage"] {
  background: transparent !important;
}
[data-testid="stChatMessage"] .st-emotion-cache-ue6h4q {
  background: #111827 !important;
  border: 1px solid rgba(148,163,184,0.15) !important;
  border-radius: 16px !important;
}

/* Code blocks */
pre, code, .stCodeBlock {
  background: #0f172a !important;
  color: #e5e7eb !important;
  border-radius: 12px !important;
  border: 1px solid rgba(148,163,184,0.18) !important;
}

/* Buttons */
.stButton > button {
  background: #1f2937 !important;
  color: #fff !important;
  border: 1px solid rgba(148,163,184,0.18) !important;
  border-radius: 12px !important;
}
.stButton > button:hover { filter: brightness(1.08); }

/* Text inputs */
.stTextInput > div > div > input {
  background: #0f172a !important;
  color: #e5e7eb !important;
}

/* Expander */
.streamlit-expanderHeader {
  background: #0f172a !important;
}
</style>
"""
st.markdown(CHATGPT_DARK, unsafe_allow_html=True)

# ============================ MCP CLIENT ============================

def _parse_sse(text: str):
    """Parse Server-Sent Events (SSE) payload and return last JSON data line.
    Falls back to plain JSON if not SSE."""
    try:
        # SSE: find the last `data: {...}` line
        last = None
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("data: "):
                last = line[6:]
        if last:
            return json.loads(last)
        # Plain JSON fallback
        return json.loads(text)
    except Exception:
        raise Exception("Invalid MCP response: " + text[:500])


def call_mcp(method: str, params: dict | None = None, *, timeout: int = 60):
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or {},
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if MCP_BEARER:
        headers["Authorization"] = f"Bearer {MCP_BEARER}"

    try:
        resp = requests.post(MCP_SERVER_URL, headers=headers, json=payload, timeout=timeout)
    except requests.RequestException as e:
        raise Exception(f"MCP connection failed: {e}")

    if resp.status_code >= 400:
        raise Exception(f"MCP HTTP {resp.status_code}: {resp.text[:300]}")

    return _parse_sse(resp.text)


def list_tools() -> list[str]:
    try:
        resp = call_mcp("rpc.discover")
        return [t.get("name", "") for t in resp.get("result", {}).get("tools", [])]
    except Exception:
        return []

# ============== CLASSIFIER: WHEN TO USE MCP VS MODEL =================
K8S_KEYWORDS = [
    "kubernetes", "k8s", "cluster", "pod", "pods", "node", "nodes",
    "namespace", "namespaces", "service", "services", "deployment",
    "daemonset", "statefulset", "ingress", "helm", "context", "cronjob",
    "port-forward", "events", "logs", "exec", "rollout", "configmap",
    "secrets", "rds", "eks", "karpenter", "kyverno"
]


def should_use_mcp(question: str) -> bool:
    q = question.lower()
    return any(k in q for k in K8S_KEYWORDS) or "mcp" in q


# ====================== QUESTION → TOOL MAPPING ======================
# 1) Quick, rule-based hints for popular intents (no LLM latency)
RULE_MAP = [
    ("how many namespaces", {
        "name": "kubectl_get",
        "arguments": {"resource": "namespaces", "output": "json"}
    }),
    ("how many pods", {
        "name": "kubectl_get",
        "arguments": {"resource": "pods", "all_namespaces": True}
    }),
    ("current context", {
        "name": "current_context",
        "arguments": {}
    }),
    ("list contexts", {
        "name": "list_contexts",
        "arguments": {}
    }),
]


def rule_map(question: str):
    q = question.lower()
    for key, tool in RULE_MAP:
        if key in q:
            return tool
    return None


# 2) LLM mapping as a flexible fallback
MAPPING_INSTR = (
    "You map user questions to a single MCP tool call. "
    "Return STRICT JSON only: {\n  \"name\": \"<tool_name>\",\n  \"arguments\": { }\n}\n"
)


def llm_map(question: str, tools: list[str]):
    if not model:
        return None
    prompt = f"""
{MAPPING_INSTR}
Available tools: {tools}
User question: "{question}"
"""
    try:
        out = model.generate_content(prompt).text.strip()
        if out.startswith("```"):
            out = out.strip("` ")
            out = out.replace("json", "", 1).strip()
        return json.loads(out)
    except Exception:
        return None


# ===================== HIGH-LEVEL ANSWER FLOWS =======================

def ask_cluster(question: str) -> tuple[str, dict | None]:
    """Use MCP for cluster/MCP questions. Return (message, debug_payload)."""
    tools = list_tools()
    if not tools:
        return ("⚠ MCP server tools not discovered. Check MCP_SERVER_URL / token.", None)

    # Prefer fast rule match, else LLM mapping, else give a generic failure
    mapping = rule_map(question) or llm_map(question, tools)
    if not mapping:
        return ("⚠ Couldn't map your request to an MCP tool.", {"tools": tools})

    # Call the MCP tool
    try:
        mcp_resp = call_mcp("tools/call", {
            "name": mapping["name"],
            "arguments": mapping.get("arguments", {}),
        })
        # Normalize response
        result = mcp_resp.get("result", mcp_resp)
        # Decide success heuristic
        success = True
        if isinstance(result, dict) and result.get("error"):
            success = False

        # Summarize result for the user (short)
        pretty = json.dumps(result, indent=2)
        short = f"✅ MCP executed: **{mapping['name']}**\n\n" \
                f"**Arguments:** `{json.dumps(mapping.get('arguments', {}))}`\n\n" \
                f"**Status:** {'success' if success else 'failed'}\n\n" \
                f"**Raw (trimmed):**\n```json\n{pretty[:2000]}\n```"
        return (short, {"mapping": mapping, "raw": result})

    except Exception as e:
        return (f"❌ MCP execution failed for **{mapping['name']}**: {e}", {"mapping": mapping})


def ask_normal(question: str) -> str:
    if not model:
        return "(No model configured)"
    return model.generate_content(question).text


def ask(question: str) -> tuple[str, dict | None]:
    if should_use_mcp(question):
        return ask_cluster(question)
    # General chat → do NOT touch MCP
    return (ask_normal(question), None)

# ============================== SIDEBAR ==============================
st.sidebar.title("☁ MASA Bot")
st.sidebar.caption("MCP + Gemini | Dark UI")
with st.sidebar.expander("Server config", expanded=False):
    st.write("**MCP URL:**", MCP_SERVER_URL)
    st.write("**Model:**", GEMINI_MODEL or "–")
    st.write("**Auth:**", "Bearer set" if MCP_BEARER else "(none)")

with st.sidebar.expander("Quick prompts"):
    if st.button("How many namespaces?"):
        st.session_state.setdefault("history", []).append(("user", "How many namespaces in my cluster?"))
    if st.button("Current context"):
        st.session_state.setdefault("history", []).append(("user", "Get current context"))
    if st.button("How many pods?"):
        st.session_state.setdefault("history", []).append(("user", "How many pods running in cluster"))

# ============================== CHAT UI ==============================
st.title("☁ MASA Bot")
st.caption("Kubernetes-aware chat. Uses MCP only for cluster/MCP questions; answers normally for everything else.")

if "history" not in st.session_state:
    st.session_state.history = []

# Render history
for role, text in st.session_state.history:
    with st.chat_message(role):
        st.markdown(text)

# Chat input
if user_q := st.chat_input("Ask about your cluster or anything…"):
    st.session_state.history.append(("user", user_q))
    with st.chat_message("user"):
        st.markdown(user_q)

    try:
        answer, debug = ask(user_q)
    except Exception as e:
        answer, debug = (f"⚠ {e}", None)

    st.session_state.history.append(("assistant", answer))
    with st.chat_message("assistant"):
        st.markdown(answer)
        if debug:
            with st.expander("Debug details"):
                st.write(debug)


