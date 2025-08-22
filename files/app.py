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

# Load MCP tools config
TOOLS_PATH = os.path.join(os.getcwd(), "mcp_tools.json")
with open(TOOLS_PATH, "r") as f:
    MCP_TOOLS = json.load(f)


def mcp_route(tool_name: str):
    """Pick MCP server by tool name regex mapping."""
    for rule in MCP_CFG.get("routing", []):
        if re.search(rule["matcher"], tool_name, flags=re.I):
            name = rule["server"]
            for srv in MCP_CFG.get("servers", []):
                if srv["name"] == name:
                    return srv
    return None


def call_mcp_http(server, tool_name: str):
    """Call MCP server with the tool name."""
    base = server["baseUrl"].rstrip("/")
    headers = {}
    authHeader = server.get("authHeader")
    if authHeader:
        expanded = re.sub(r"\$\{([^}]+)\}", lambda m: os.getenv(m.group(1), ""), authHeader)
        headers["Authorization"] = expanded

    try:
        resp = requests.post(f"{base}/query", json={"prompt": tool_name}, headers=headers, timeout=60)
        if resp.status_code == 404:
            resp = requests.post(f"{base}/chat", json={"prompt": tool_name}, headers=headers, timeout=60)
        resp.raise_for_status()
        js = resp.json()
        return js.get("result") or js.get("answer") or js.get("message") or js.get("content") or json.dumps(js)
    except Exception as e:
        return f"[MCP:{server['name']}] error: {e}"


def call_ollama(prompt: str, system=None, model="mistral:7b-instruct-v0.2-q4_0"):
    """Call Ollama to generate text."""
    payload = {
        "model": model,
        "prompt": f"{system or 'You are MasaBot, a helpful DevOps assistant.'}\n\n{prompt}",
        "stream": False
    }
    r = requests.post(f"{OLLAMA_BASE}/api/generate", json=payload, timeout=60)
    r.raise_for_status()
    return r.json().get("response", "").strip()


def translate_to_mcp(user_text: str):
    """Use Ollama to translate natural language into MCP tool JSON."""
    system_prompt = f"""
You are a command translator for MCP.
Available tools:
{json.dumps(MCP_TOOLS, indent=2)}

Rules:
- Always return valid JSON with keys: "tool" and "args".
- "args" can be empty if not needed.
- Do not explain, just return JSON.
    """

    payload = {
        "model": "mistral:7b-instruct-v0.2-q4_0",
        "prompt": f"{system_prompt}\n\nUser: {user_text}\nAssistant:",
        "stream": False
    }
    try:
        r = requests.post(f"{OLLAMA_BASE}/api/generate", json=payload, timeout=60)
        r.raise_for_status()
        js = r.json()
        raw = js.get("response", "{}").strip()
        return json.loads(raw)
    except Exception as e:
        return {"error": str(e)}


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

    with st.spinner("Thinking with Ollama…"):
        translation = translate_to_mcp(user_text)

    if "tool" in translation:
        tool = translation["tool"]
        args = translation.get("args", {})
        target = mcp_route(tool)
        if target:
            with st.spinner(f"Calling MCP tool: {tool}"):
                answer = call_mcp_http(target, tool)
        else:
            answer = f"⚠️ No MCP server found for tool: {tool}"
    else:
        answer = call_ollama(user_text)

    msgs.append({"role": "assistant", "content": answer})
    st.markdown(f"<div class='chat-bubble-bot'>{answer}</div>", unsafe_allow_html=True)

if not st.session_state.current.get("title") and msgs:
    st.session_state.current["title"] = (msgs[0]["content"][:30] + "…") if len(msgs[0]["content"]) > 30 else msgs[0]["content"]
