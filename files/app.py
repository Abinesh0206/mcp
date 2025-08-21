import os, json, re, requests
import streamlit as st

# ---------- Config ----------
OLLAMA_BASE = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
TITLE = os.getenv("UI_TITLE", "MasaBot")
PRIMARY = os.getenv("THEME_PRIMARY", "#1e88e5")
ACCENT = os.getenv("THEME_ACCENT", "#ff6f00")

# Load MCP routing config
CONFIG_PATH = os.path.join(os.getcwd(), "mcp_config.json")
with open(CONFIG_PATH, "r") as f:
    MCP_CFG = json.load(f)

def mcp_route(user_text: str):
    """Pick MCP server by regex routing rules."""
    for rule in MCP_CFG.get("routing", []):
        if re.search(rule["matcher"], user_text, flags=re.I):
            name = rule["server"]
            for srv in MCP_CFG.get("servers", []):
                if srv["name"] == name:
                    return srv
    return None

def call_mcp_http(server, user_text: str):
    """Call MCP server. Supports /query or /chat automatically."""
    base = server["baseUrl"].rstrip("/")
    headers = {}
    authHeader = server.get("authHeader")
    if authHeader:
        expanded = re.sub(r"\$\{([^}]+)\}", lambda m: os.getenv(m.group(1), ""), authHeader)
        headers["Authorization"] = expanded

    try:
        # First try /query
        resp = requests.post(f"{base}/query", json={"query": user_text}, headers=headers, timeout=60)
        if resp.status_code == 404:  # fallback to /chat
            resp = requests.post(f"{base}/chat", json={"prompt": user_text}, headers=headers, timeout=60)
        resp.raise_for_status()
        js = resp.json()
        return js.get("result") or js.get("answer") or js.get("message") or json.dumps(js)
    except Exception as e:
        return f"[MCP:{server['name']}] error: {e}"

def call_ollama(user_text: str, system=None, model="mistral:7b-instruct-v0.2-q4_0"):
    """Call Ollama /api/generate."""
    payload = {
        "model": model,
        "prompt": f"{system or 'You are MasaBot, a helpful DevOps assistant.'}\n\nUser: {user_text}\nAssistant:",
        "stream": False
    }
    try:
        r = requests.post(f"{OLLAMA_BASE}/api/generate", json=payload, timeout=120)
        r.raise_for_status()
        js = r.json()
        return js.get("response") or js.get("message") or json.dumps(js)
    except Exception as e:
        return f"[Ollama] error: {e}"

# ---------- UI ----------
st.set_page_config(page_title=TITLE, page_icon="🤖", layout="wide")
st.markdown(f"""
<style>
  .stApp {{ background: white; }}
  .chat-bubble-user {{
    border-left: 4px solid {PRIMARY}; padding: 12px; margin: 8px 0;
    border-radius: 12px; background: #f5f9ff;
  }}
  .chat-bubble-bot {{
    border-left: 4px solid {ACCENT}; padding: 12px; margin: 8px 0;
    border-radius: 12px; background: #fff8f0;
  }}
  .history-item {{ cursor:pointer; padding:8px; margin:4px 0;
    border-radius:10px; border:1px solid #eee; }}
  .history-item:hover {{ border-color: {PRIMARY}; background:#f9fbff; }}
  .title {{ font-size: 28px; font-weight: 700; color: {PRIMARY}; }}
</style>
""", unsafe_allow_html=True)

if "sessions" not in st.session_state:
    st.session_state.sessions = []
if "current" not in st.session_state:
    st.session_state.current = {"title": "New chat", "messages": []}

with st.sidebar:
    st.markdown(f"<div class='title'>🧠 {TITLE}</div>", unsafe_allow_html=True)
    if st.button("➕ New chat"):
        if st.session_state.current["messages"]:
            st.session_state.sessions.append(st.session_state.current)
        st.session_state.current = {"title": "New chat", "messages": []}
    st.markdown("---")
    st.subheader("History")
    for i, s in enumerate(reversed(st.session_state.sessions)):
        idx = len(st.session_state.sessions) - 1 - i
        if st.button(s["title"] or f"Chat {idx+1}", key=f"hist-{idx}"):
            st.session_state.sessions.append(st.session_state.current)
            st.session_state.current = s
            del st.session_state.sessions[idx]
    st.markdown("---")
    st.caption("Blue = you, Orange = MasaBot. MCP auto-routes by keywords (k8s/argo/jenkins).")

st.markdown("### Start chatting")
user_text = st.chat_input("Type your message…")
msgs = st.session_state.current["messages"]

for m in msgs:
    if m["role"] == "user":
        st.markdown(f"<div class='chat-bubble-user'>{m['content']}</div>", unsafe_allow_html=True)
    else:
        st.markdown(f"<div class='chat-bubble-bot'>{m['content']}</div>", unsafe_allow_html=True)

if user_text:
    msgs.append({"role": "user", "content": user_text})
    st.markdown(f"<div class='chat-bubble-user'>{user_text}</div>", unsafe_allow_html=True)

    target = mcp_route(user_text)
    if target:
        with st.spinner(f"Querying MCP: {target['name']}"):
            answer = call_mcp_http(target, user_text)
    else:
        with st.spinner("Thinking with Ollama…"):
            answer = call_ollama(user_text, model="mistral:7b-instruct-v0.2-q4_0")

    msgs.append({"role": "assistant", "content": answer})
    st.markdown(f"<div class='chat-bubble-bot'>{answer}</div>", unsafe_allow_html=True)

if not st.session_state.current.get("title") and msgs:
    st.session_state.current["title"] = (msgs[0]["content"][:30] + "…") if len(msgs[0]["content"]) > 30 else msgs[0]["content"]
