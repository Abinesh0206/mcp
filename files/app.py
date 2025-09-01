import os, json, re, requests
import streamlit as st
import google.generativeai as genai

# ---------- Config ----------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyC7iRO4NnyQz144aEc6RiVUNzjL9C051V8")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

TITLE = os.getenv("UI_TITLE", "MasaBot")
PRIMARY = os.getenv("THEME_PRIMARY", "#1e88e5")
ACCENT = os.getenv("THEME_ACCENT", "#ff6f00")

MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://18.234.91.216:3000/mcp")

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json"
}

# Configure Gemini
genai.configure(api_key=GEMINI_API_KEY)

# ---------- Helpers ----------
def mcp_request(payload):
    try:
        response = requests.post(
            MCP_SERVER_URL, 
            json=payload, 
            headers=HEADERS, 
            timeout=30
        )
        
        if response.status_code != 200:
            return {"error": f"Status {response.status_code}", "body": response.text}
        
        return response.json()
            
    except Exception as e:
        return {"error": str(e)}

def call_gemini(user_text: str, system_prompt=None):
    try:
        # Prepare the prompt with system instructions
        full_prompt = f"""{system_prompt or "You are MasaBot, a DevOps AI assistant."}

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

        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(full_prompt)
        return response.text.strip()
            
    except Exception as e:
        return f"[Gemini] error: {e}"

def parse_kubernetes_query(query):
    """Parse natural language query into MCP tool arguments"""
    query_lower = query.lower()
    
    # Count pods queries
    if "how many pods" in query_lower or "count pods" in query_lower:
        namespace = "default"
        if "kube-system" in query_lower:
            namespace = "kube-system"
        elif "all namespaces" in query_lower or "cluster" in query_lower:
            return "kubectl_get", {"resourceType": "pods", "allNamespaces": True, "output": "json"}
        
        return "kubectl_get", {"resourceType": "pods", "namespace": namespace, "output": "json"}
    
    # Get specific resources
    elif "get " in query_lower:
        parts = query_lower.split("get ")
        if len(parts) > 1:
            resource_part = parts[1].split()[0]
            namespace = "default"
            if "kube-system" in query_lower:
                namespace = "kube-system"
            elif "all namespaces" in query_lower:
                return "kubectl_get", {"resourceType": resource_part, "allNamespaces": True, "output": "json"}
            
            return "kubectl_get", {"resourceType": resource_part, "namespace": namespace, "output": "json"}
    
    # Create namespace
    elif "create namespace" in query_lower:
        namespace_name = query_lower.replace("create namespace", "").strip()
        return "kubectl_create", {"resourceType": "namespace", "name": namespace_name}
    
    # Delete namespace
    elif "delete namespace" in query_lower:
        namespace_name = query_lower.replace("delete namespace", "").strip()
        return "kubectl_delete", {"resourceType": "namespace", "name": namespace_name}
    
    # Describe resources
    elif "describe " in query_lower:
        parts = query_lower.split("describe ")
        if len(parts) > 1:
            resource_part = parts[1].split()[0]
            namespace = "default"
            if "kube-system" in query_lower:
                namespace = "kube-system"
            
            # Extract resource name if provided (e.g., "describe pod nginx")
            name_parts = parts[1].split()
            name = name_parts[1] if len(name_parts) > 1 else ""
            
            if name:
                return "kubectl_describe", {"resourceType": resource_part, "name": name, "namespace": namespace}
            else:
                return "kubectl_describe", {"resourceType": resource_part, "namespace": namespace}
    
    return None, {}

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
  .mcp-response {{
    border: 1px solid #ddd; padding: 12px; margin: 8px 0;
    border-radius: 8px; background: #f9f9f9;
    font-family: monospace; font-size: 14px;
  }}
</style>
""", unsafe_allow_html=True)

if "sessions" not in st.session_state:
    st.session_state.sessions = []
if "current" not in st.session_state:
    st.session_state.current = {"title": "New chat", "messages": []}

st.title("🤖 MasaBot – MCP + Gemini UI")
st.markdown(f"### 🔗 Connected to MCP server: {MCP_SERVER_URL}")

# Display connection status
col1, col2 = st.columns(2)
with col1:
    if st.button("Test MCP Connection"):
        try:
            ping_payload = {
                "jsonrpc": "2.0",
                "id": "ping-test",
                "method": "ping",
                "params": {}
            }
            response = mcp_request(ping_payload)
            if "result" in response:
                st.success("✅ MCP Server is connected and responsive!")
            else:
                st.error(f"❌ MCP Server error: {response.get('error', 'Unknown error')}")
        except Exception as e:
            st.error(f"❌ Cannot connect to MCP Server: {str(e)}")

with col2:
    if st.button("Show Available Tools"):
        tools_payload = {
            "jsonrpc": "2.0",
            "id": "tools-list",
            "method": "tools/list",
            "params": {}
        }
        tools_response = mcp_request(tools_payload)
        if "result" in tools_response and "tools" in tools_response["result"]:
            st.markdown("### 🛠️ Available Tools")
            for tool in tools_response["result"]["tools"]:
                st.write(f"**{tool['name']}**: {tool['description']}")
        else:
            st.error("Could not retrieve tools list")

st.markdown("---")
st.markdown("### 💬 Start chatting")

user_text = st.chat_input("Type your Kubernetes query or general question...")
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
            if parsed["target"] == "kubernetes":
                # Parse the query for MCP
                tool_name, tool_args = parse_kubernetes_query(parsed["query"])
                
                if tool_name:
                    # Get tools list first
                    tools_payload = {
                        "jsonrpc": "2.0",
                        "id": "tools-list",
                        "method": "tools/list",
                        "params": {}
                    }
                    tools_response = mcp_request(tools_payload)
                    
                    if "result" in tools_response and "tools" in tools_response["result"]:
                        tools = tools_response["result"]["tools"]
                        tool_exists = any(tool["name"] == tool_name for tool in tools)
                        
                        if tool_exists:
                            with st.spinner(f"Querying MCP Kubernetes: {tool_name}"):
                                call_payload = {
                                    "jsonrpc": "2.0",
                                    "id": "mcp-call",
                                    "method": "tools/call",
                                    "params": {
                                        "name": tool_name,
                                        "arguments": tool_args
                                    }
                                }
                                mcp_result = mcp_request(call_payload)
                            
                            # Format MCP response
                            if "result" in mcp_result and "content" in mcp_result["result"]:
                                mcp_content = mcp_result["result"]["content"]
                                if isinstance(mcp_content, list) and len(mcp_content) > 0:
                                    content_text = mcp_content[0].get("text", "")
                                    
                                    # Try to parse as JSON for better formatting
                                    try:
                                        parsed_json = json.loads(content_text)
                                        formatted_response = json.dumps(parsed_json, indent=2)
                                        
                                        # If it's a list of pods, count them
                                        if isinstance(parsed_json, dict) and "items" in parsed_json:
                                            pod_count = len(parsed_json["items"])
                                            answer = f"✅ MCP Kubernetes Response:\n\nFound {pod_count} items\n\n```json\n{formatted_response}\n```"
                                        else:
                                            answer = f"✅ MCP Kubernetes Response:\n\n```json\n{formatted_response}\n```"
                                            
                                    except json.JSONDecodeError:
                                        answer = f"✅ MCP Kubernetes Response:\n\n{content_text}"
                                else:
                                    answer = f"✅ MCP Kubernetes Response:\n\n{json.dumps(mcp_result, indent=2)}"
                            else:
                                answer = f"❌ MCP Error:\n\n{json.dumps(mcp_result, indent=2)}"
                        else:
                            answer = f"❌ Tool '{tool_name}' not found in MCP server"
                    else:
                        answer = f"❌ Could not retrieve tools from MCP server"
                else:
                    answer = f"❌ Could not parse query: {parsed['query']}"
            else:
                answer = f"❌ Only Kubernetes target is currently supported. Requested: {parsed['target']}"
        else:
            answer = gemini_answer
    except json.JSONDecodeError:
        # Normal text response from Gemini
        answer = gemini_answer
    except Exception as e:
        answer = f"❌ Error processing response: {str(e)}"

    msgs.append({"role": "assistant", "content": answer})
    st.markdown(f"<div class='chat-bubble-bot'>{answer}</div>", unsafe_allow_html=True)

if not st.session_state.current.get("title") and msgs:
    st.session_state.current["title"] = msgs[0]["content"][:30] + "…"

# Add examples
st.sidebar.markdown("## 💡 Example Queries")
examples = [
    "How many pods in kube-system namespace?",
    "Get all pods in default namespace",
    "Create namespace my-app",
    "Delete namespace test",
    "Describe all services in kube-system"
]

for example in examples:
    if st.sidebar.button(example, key=f"example_{example}"):
        st.experimental_set_query_params(query=example)
        st.experimental_rerun()
