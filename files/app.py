import os, json, re, requests
import streamlit as st

# ---------- Config ----------
OLLAMA_BASE = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
TITLE = os.getenv("UI_TITLE", "MasaBot")
PRIMARY = os.getenv("THEME_PRIMARY", "#1e88e5")
ACCENT = os.getenv("THEME_ACCENT", "#ff6f00")

CONFIG_PATH = os.path.join(os.getcwd(), "mcp_config.json")
with open(CONFIG_PATH, "r") as f:
    MCP_CFG = json.load(f)

# ---------- Helpers ----------
def call_mcp_http(server, query: str):
    base = server["baseUrl"].rstrip("/")
    headers = {}
    if server.get("authHeader"):
        expanded = re.sub(r"\$\{([^}]+)\}", lambda m: os.getenv(m.group(1), ""), server["authHeader"])
        headers["Authorization"] = expanded
    try:
        resp = requests.post(f"{base}/query", json={"prompt": query}, headers=headers, timeout=60)
        if resp.status_code == 404:
            resp = requests.post(f"{base}/chat", json={"prompt": query}, headers=headers, timeout=60)
        resp.raise_for_status()
        js = resp.json()
        return js.get("result") or js.get("answer") or js.get("message") or js.get("content") or json.dumps(js)
    except Exception as e:
        return f"[MCP:{server['name']}] error: {e}"

def call_ollama(user_text: str, system=None, model="mistral:7b-instruct-v0.2-q4_0"):
    payload = {
        "model": model,
        "prompt": f"""{system or "You are MasaBot, a DevOps AI assistant."}

User may ask two types of questions:
1. General/explanatory → answer directly in plain text.
2. Live/system query (Kubernetes, ArgoCD, Jenkins) → DO NOT answer directly. Instead, respond ONLY in JSON like this:
   {{ "target": "k8s", "query": "get pods in all namespaces" }}
   or
   {{ "target": "jenkins", "query": "list all jobs" }}

User: {user_text}
Assistant:""",
        "stream": False
    }
    try:
        r = requests.post(f"{OLLAMA_BASE}/api/generate", json=payload, timeout=120)
        r.raise_for_status()
        js = r.json()
        return js.get("response", "").strip()
    except Exception as e:
        return f"[Ollama] error: {e}"

def get_server_by_name(name: str):
    for srv in MCP_CFG.get("servers", []):
        if srv["name"].lower() == name:
            return srv
    return None

# ---------- UI ----------
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

    with st.spinner("Ollama thinking…"):
        ollama_answer = call_ollama(user_text)

    # Try parse as JSON → means MCP request
    try:
        parsed = json.loads(ollama_answer)
        if isinstance(parsed, dict) and "target" in parsed and "query" in parsed:
            server = get_server_by_name(parsed["target"])
            if server:
                with st.spinner(f"Querying MCP: {parsed['target']}"):
                    mcp_result = call_mcp_http(server, parsed["query"])
                answer = f"From MCP:{parsed['target']} → {mcp_result}"
            else:
                answer = f"[Error] No MCP server found for: {parsed['target']}"
        else:
            answer = ollama_answer
    except Exception:
        # Normal text response
        answer = ollama_answer

    msgs.append({"role": "assistant", "content": answer})
    st.markdown(f"<div class='chat-bubble-bot'>{answer}</div>", unsafe_allow_html=True)

if not st.session_state.current.get("title") and msgs:
    st.session_state.current["title"] = msgs[0]["content"][:30] + "…"
