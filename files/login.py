# login.py
import streamlit as st
import pymongo
import bcrypt
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://vigneshkavi_db_user:admin123@mcp.cautaos.mongodb.net/mcp_auth?retryWrites=true&w=majority")
DB_NAME = os.getenv("DB_NAME", "mcp_auth")

# Initialize MongoDB
@st.cache_resource
def init_db():
    client = pymongo.MongoClient(MONGO_URI)
    db = client[DB_NAME]
    return db

def hash_password(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())

def verify_password(password, hashed):
    return bcrypt.checkpw(password.encode('utf-8'), hashed)

def create_user(username, password, permissions):
    db = init_db()
    if db.users.find_one({"username": username}):
        return False, "User already exists"
    
    hashed = hash_password(password)
    db.users.insert_one({
        "username": username,
        "password": hashed,
        "permissions": permissions,  # List of allowed server URLs
        "created_at": st.session_state.get("now", None)
    })
    return True, "User created successfully"

def authenticate_user(username, password):
    db = init_db()
    user = db.users.find_one({"username": username})
    if user and verify_password(password, user["password"]):
        return True, user["permissions"]
    return False, []

# Streamlit UI
st.set_page_config(page_title="MCP Login", layout="centered")

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
    st.session_state.username = ""
    st.session_state.permissions = []

if not st.session_state.authenticated:
    st.title("🔐 MCP Server Login")
    
    # Tabs for Login/Register
    tab1, tab2 = st.tabs(["Login", "Register"])
    
    # Login Tab
    with tab1:
        with st.form("login_form"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submit = st.form_submit_button("Login")
            
            if submit:
                success, permissions = authenticate_user(username, password)
                if success:
                    st.session_state.authenticated = True
                    st.session_state.username = username
                    st.session_state.permissions = permissions
                    st.success("Login successful!")
                    st.rerun()
                else:
                    st.error("Invalid credentials")
    
    # Register Tab (Admin-only in production)
    with tab2:
        with st.form("register_form"):
            new_user = st.text_input("New Username")
            new_pass = st.text_input("New Password", type="password")
            # In production, load servers from your servers.json
            server_urls = [
                "http://k8s-mcp.local:8080",
                "http://jenkins-mcp.local:8080",
                "http://argocd-mcp.local:8080"
            ]
            selected_servers = st.multiselect(
                "Grant access to servers",
                options=server_urls,
                default=server_urls
            )
            register = st.form_submit_button("Create User")
            
            if register:
                if new_user and new_pass:
                    success, msg = create_user(new_user, new_pass, selected_servers)
                    if success:
                        st.success(msg)
                    else:
                        st.error(msg)
                else:
                    st.error("Fill all fields")
else:
    # Show main app after login
    st.success(f"Welcome back, {st.session_state.username}!")
    st.write("Your permissions:", st.session_state.permissions)
    
    if st.button("Logout"):
        st.session_state.authenticated = False
        st.session_state.username = ""
        st.session_state.permissions = []
        st.rerun()
    
    # Redirect to main app
    st.markdown("### [Go to MCP Dashboard](main.py)")
