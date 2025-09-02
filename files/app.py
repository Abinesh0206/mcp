import streamlit as st
import requests
import json

# ---------------- CONFIG ----------------
MCP_SERVER_URL = "http://18.234.91.216:3000/mcp"
GEMINI_API_KEY = "AIzaSyA-iOGmYUxW000Nk6ORFFopi3cJE7J8wA4"
GEMINI_MODEL = "gemini-1.5-flash"

# ---------------- UI CONFIG ----------------
st.set_page_config(page_title="MasaBot", page_icon="🤖", layout="centered")

# Custom CSS for blue + orange glacier theme
st.markdown(
    """
    <style>
    body {
        background: linear-gradient(135deg, #1E3C72, #2A5298, #FF6B35, #FFB347);
        background-size: 400% 400%;
        animation: gradientBG 15s ease infinite;
        color: white;
    }
    @keyframes gradientBG {
        0% {background-position: 0% 50%;}
        50% {background-position: 100% 50%;}
        100% {background-position: 0% 50%;}
    }
    .stChatMessage {
        border-radius: 12px;
        padding: 10px;
        margin: 5px 0px;
    }
    .user-msg {
        background-color: rgba(255, 179, 71, 0.8);
        color: black;
        text-align: right;
    }
    .bot-msg {
        background-color: rgba(30, 60, 114, 0.8);
        color: white;
        text-align: left;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🤖 MasaBot – MCP + Gemini UI")

# ---------------- SESSION STATE ----------------
if "messages" not in st.session_state:
    st.session_state["messages"] = []

# ---------------- MCP CALL ----------------
def call_mcp_server(query: str):
    """Send query to MCP server and return response."""
    try:
        payload = {"query": query}
        headers = {"Content-Type": "application/json"}
        resp = requests.post(MCP_SERVER_URL, headers=headers, data=json.dumps(payload), timeout=30)
        if resp.status_code == 200:
            return resp.json()
        else:
            return {"error": f"Server error: {resp.status_code}"}
    except Exception as e:
        return {"error": str(e)}

# ---------------- CHAT DISPLAY ----------------
for msg in st.session_state["messages"]:
    role = msg["role"]
    if role == "user":
        st.markdown(f"<div class='stChatMessage user-msg'>💬 {msg['content']}</div>", unsafe_allow_html=True)
    else:
        st.markdown(f"<div class='stChatMessage bot-msg'>🤖 {msg['content']}</div>", unsafe_allow_html=True)

# ---------------- INPUT BOX ----------------
user_input = st.chat_input("Type your message here...")

if user_input:
    # Save user message
    st.session_state["messages"].append({"role": "user", "content": user_input})

    # Call MCP server
    response = call_mcp_server(user_input)

    # Parse response
    if "error" in response:
        bot_reply = f"⚠️ Error: {response['error']}"
    else:
        bot_reply = json.dumps(response, indent=2)

    # Save bot response
    st.session_state["messages"].append({"role": "bot", "content": bot_reply})

    # Rerun to refresh UI
    st.experimental_rerun()
