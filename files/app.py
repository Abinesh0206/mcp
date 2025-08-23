import os, json, re, requests
import streamlit as st

# ---------- Config ----------
OLLAMA_BASE = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
TITLE = os.getenv("UI_TITLE", "MasaBot")
PRIMARY = os.getenv("THEME_PRIMARY", "#1e88e5")
ACCENT = os.getenv("THEME_ACCENT", "#ff6f00")

# Load MCP routing config (define all MCP servers in mcp_config.json)
CONFIG_PATH = os.path.join(os.getcwd(), "mcp_config.json")
with open(CONFIG_PATH, "r") as f:
    MCP_CFG = json.load(f)


# ---------- Helpers ----------
def call_mcp_http(server, user_text: str):
    """Call MCP server (supports /query and /chat)."""
    base = server["baseUrl"].rstrip("/")
    headers = {}
    if server.get("authHeader"):
        expanded = re.sub(r"\$\{([^}]+)\}", lambda m: os.getenv(m.group(1), ""), server["authHeader"])
        headers["Authorization"] = expanded

    try:
        resp = requests.post(f"{base}/query", json={"prompt": user_text}, headers=headers, timeout=60)
        if resp.status_code == 404:
            resp = requests.post(f"{base}/chat", json={"prompt": user_text}, headers=headers, timeout=60)
        resp.raise_for_status()
        js = resp.json()
        return js.get("result") or js.get("answer") or js.get("message") or js.get("content") or json.dumps(js)
    except Exception as e:
        return f"[MCP:{server['name']}] error: {e}"


def call_ollama(user_text: str, system=None, model="mistral:7b-instruct-v0.2-q4_0"):
    """Call Ollama /api/generate (streaming)."""
    payload = {
        "model": model,
        "prompt": f"{system or 'You are MasaBot, a helpful DevOps assistant.'}\n\nUser: {user_text}\nAssistant:",
        "stream": True
    }
    try:
        r = requests.post(f"{OLLAMA_BASE}/api/generate", json=payload, stream=True, timeout=120)
        r.raise_for_status()
        response_text = ""
        for line in r.iter_lines():
            if not line:
                continue
            js = json.loads(line.decode("utf-8"))
            if js.get("done"):
                break
            response_text += js.get("response", "")
        return response_text.strip()
    except Exception as e:
        return f"[Ollama] error: {e}"


def classify_intent(user_text: str):
    """Ask Ollama: general chat or which MCP server?"""
    system = """
You are an intent classifier.
Decide if the user wants:
- 'chat' → general explanation (no live query)
- Or which MCP server (pick from config): return one server name from this list: 
{}
If you cannot match, return 'chat'.
Return only one word.
""".format(", ".join([srv["name"] for srv in MCP_CFG.get("servers", [])]))
    resp = call_ollama(user_text, system=system, model="mistral:7b-instruct-v0.2-q4_0")
    return resp.strip().lower()


def get_server_by_name(name: str):
    for srv in MCP_CFG.get("servers", []):
        if srv["name"].lower() == name:
            return srv
    return None


# ---------- UI ----------
st.set_page_config(page_title=TITLE, page_icon="🤖", layout="wide")
st.markdown(f"""
<style>
  .stApp {{
    background: linear-gradient(135deg, {PRIMARY}22, {ACCENT}22, #ffffff);
    background-size: 400% 400%;
    animation: gradientBG 15s ease infinite;
  }}
  section[data-testid="stSidebar"] {{
    background: linear-gradient(135deg, {PRIMARY}33, {ACCENT}22, #fafafa);
    background-size: 400% 400%;
    animation: gradientBG 20s ease infinite;
  }}
  @keyframes gradientBG {{
    0% {{background-position: 0% 50%;}}
    50% {{background-position: 100% 50%;}}
    100% {{background-position: 0% 50%;}}
  }}
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

with st.sidebar:
    st.markdown(f"<div style='font-size:28px; font-weight:700; color:{PRIMARY};'>🧠 {TITLE}</div>", unsafe_allow_html=True)
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

    # Step 1: Ollama classifies intent
    intent = classify_intent(user_text)

    if intent == "chat":
        with st.spinner("Thinking with Ollama…"):
            answer = call_ollama(user_text, model="mistral:7b-instruct-v0.2-q4_0")
    else:
        server = get_server_by_name(intent)
        if server:
            with st.spinner(f"Querying MCP: {server['name']}"):
                answer = call_mcp_http(server, user_text)
        else:
            answer = call_ollama(user_text, model="mistral:7b-instruct-v0.2-q4_0")

    msgs.append({"role": "assistant", "content": answer})
    st.markdown(f"<div class='chat-bubble-bot'>{answer}</div>", unsafe_allow_html=True)

if not st.session_state.current.get("title") and msgs:
    st.session_state.current["title"] = msgs[0]["content"][:30] + "…"
