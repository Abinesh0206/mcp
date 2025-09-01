import os, json, re, requests
import streamlit as st
import google.generativeai as genai

# ---------- Config ----------
GEMINI_API_KEY = "AIzaSyC7iRO4NnyQz144aEc6RiVUNzjL9C051V8"
GEMINI_MODEL = "gemini-1.5-flash"

TITLE = os.getenv("UI_TITLE", "MasaBot")
PRIMARY = os.getenv("THEME_PRIMARY", "#1e88e5")
ACCENT = os.getenv("THEME_ACCENT", "#ff6f00")

CONFIG_PATH = os.path.join(os.getcwd(), "mcp_config.json")
with open(CONFIG_PATH, "r") as f:
    MCP_CFG = json.load(f)

# Configure Gemini
genai.configure(api_key=GEMINI_API_KEY)

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

def call_gemini(user_text: str, system=None):
    system_prompt = system or "You are MasaBot, a DevOps AI assistant."
    
    prompt = f"""{system_prompt}

User may ask two types of questions:
1. General/explanatory → answer directly in plain text.
2. Live/system query (Kubernetes, ArgoCD, Jenkins) → DO NOT answer directly. Instead, respond ONLY in JSON like this:
   {{ "target": "kubernetes", "query": "get pods in all namespaces" }}
   or
   {{ "target": "jenkins", "query": "list all jobs" }}
   or
   {{ "target": "argocd", "query": "sync app myapp" }}

⚠ Allowed targets = ["kubernetes", "argocd", "jenkins"]

User: {user_text}
Assistant:"""

    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        return f"[Gemini] error: {e}"

def get_server_by_name(name: str):
    # ---------- Alias mapping ----------
    aliases = {
        "k8s": "kubernetes",
        "kube": "kubernetes",
        "argo": "argocd",
        "cd": "argocd",
        "jenk": "jenkins"
    }
    # normalize name
    name = aliases.get(name.lower(), name.lower())

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

    with st.spinner("Gemini thinking…"):
        gemini_answer = call_gemini(user_text)

    # Try parse as JSON → means MCP request
    try:
        parsed = json.loads(gemini_answer)
        if isinstance(parsed, dict) and "target" in parsed and "query" in parsed:
            server = get_server_by_name(parsed["target"])
            if server:
                with st.spinner(f"Querying MCP: {parsed['target']}"):
                    mcp_result = call_mcp_http(server, parsed["query"])
                answer = f"From MCP:{parsed['target']} → {mcp_result}"
            else:
                answer = f"[Error] No MCP server found for: {parsed['target']}"
        else:
            answer = gemini_answer
    except Exception:
        # Normal text response
        answer = gemini_answer

    msgs.append({"role": "assistant", "content": answer})
    st.markdown(f"<div class='chat-bubble-bot'>{answer}</div>", unsafe_allow_html=True)

if not st.session_state.current.get("title") and msgs:
    st.session_state.current["title"] = msgs[0]["content"][:30] + "…"
