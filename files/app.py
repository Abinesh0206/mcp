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
def call_mcp_http(server, user_text: str):
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
    """Better classifier: general vs live query."""
    txt = user_text.lower().strip()

    # Always chat for general/explanatory
    if re.search(r"\b(what is|explain|definition|overview|tutorial|guide|how to|deploy|install)\b", txt):
        return "chat"

    # Live query keywords
    if re.search(r"\b(list|show|get|status|apps?|pods?|pipelines?|build|trigger|jobs?)\b", txt):
        if "argo" in txt or "argocd" in txt:
            return "argo"
        if "jenkins" in txt:
            return "jenkins"
        if "k8s" in txt or "kubernetes" in txt or "cluster" in txt or "pod" in txt or "namespace" in txt:
            return "k8s"

    # fallback
    return "chat"

def get_server_by_name(name: str):
    for srv in MCP_CFG.get("servers", []):
        if srv["name"].lower() == name:
            return srv
    return None

# ---------- UI ----------
st.set_page_config(page_title=TITLE, page_icon="🤖", layout="wide")

st.markdown(f"""
<style>
  /* Dark Gradient Background */
  .stApp {{
    background: linear-gradient(135deg, #0f0c29, #302b63, #24243e);
    background-size: 400% 400%;
    animation: gradientBG 15s ease infinite;
    color: #f0f0f0 !important;
  }}
  section[data-testid="stSidebar"] {{
    background: linear-gradient(135deg, #1a1a2e, #16213e, #0f3460);
    background-size: 400% 400%;
    animation: gradientBG 20s ease infinite;
    color: #fff !important;
  }}
  @keyframes gradientBG {{
    0% {{background-position: 0% 50%;}}
    50% {{background-position: 100% 50%;}}
    100% {{background-position: 0% 50%;}}
  }}

  /* Chat bubbles */
  .chat-bubble-user {{
    border-left: 4px solid #6a5acd;
    padding: 12px;
    margin: 8px 0;
    border-radius: 12px;
    background: rgba(106, 90, 205, 0.15);
    font-size: 18px;
    line-height: 1.5;
    color: #e0e0ff;
  }}
  .chat-bubble-bot {{
    border-left: 4px solid #00bfff;
    padding: 12px;
    margin: 8px 0;
    border-radius: 12px;
    background: rgba(0, 191, 255, 0.12);
    font-size: 18px;
    line-height: 1.5;
    color: #d0f0ff;
  }}

  /* Sidebar title */
  .sidebar-title {{
    font-size: 28px;
    font-weight: 700;
    color: #7b68ee;
    text-shadow: 0 0 10px rgba(123,104,238,0.8);
  }}
</style>
""", unsafe_allow_html=True)
