# app.py
import os
import json
import re
import requests
import streamlit as st
from dotenv import load_dotenv
import google.generativeai as genai

# ---------------- CONFIG ----------------
# Load environment variables from a .env file if it exists
load_dotenv()

# It's better to get the API key from Streamlit secrets or environment variables
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyBYRBa7dQ5atjlHk7e3IOdZBdo6OOcn2Pk") 
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

# Configure Gemini SDK
GEMINI_AVAILABLE = False
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        GEMINI_AVAILABLE = True
    except Exception as e:
        st.error(f"Failed to configure Gemini: {e}")
        GEMINI_AVAILABLE = False
else:
    st.warning("GEMINI_API_KEY not found. Please set it in your environment.")


# ---------------- SERVERS ----------------
def load_servers():
    """Loads server configuration from servers.json."""
    try:
        with open("servers.json") as f:
            data = json.load(f)
        # Add a success message to confirm the file was loaded
        st.sidebar.success("Loaded servers from `servers.json`!")
        return data.get("servers", []) or []
    except (FileNotFoundError, json.JSONDecodeError):
        # This is a critical warning to show the user the problem
        st.sidebar.error("`servers.json` not found or invalid! Using default localhost servers. This is likely why you cannot connect.")
        return [
            {"name": "kubernetes-mcp", "url": "http://127.0.0.1:3000/mcp", "description": "Kubernetes MCP"},
            {"name": "argocd-mcp", "url": "http://127.0.0.1:3001/mcp", "description": "ArgoCD MCP"},
            {"name": "jenkins-mcp", "url": "http://127.0.0.1:3002/mcp", "description": "Jenkins MCP"},
        ]

servers = load_servers()
server_map = {s["name"]: s["url"] for s in servers}

if "current_server" not in st.session_state and servers:
    st.session_state["current_server"] = servers[0]["url"]


# ---------------- HELPERS ----------------
def extract_json_from_string(text: str) -> dict | None:
    """Extracts a JSON object from a string, even if it's in a markdown block."""
    # Find JSON within ```json ... ```
    match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        json_str = match.group(1)
    else:
        # Fallback for plain JSON
        json_str = text[text.find("{"): text.rfind("}")+1]

    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        return None

def call_mcp_server(method: str, params: dict = None):
    """Sends a request to the currently selected MCP server."""
    url = st.session_state.get("current_server")
    if not url:
        return {"error": "No MCP server selected or configured."}
        
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    try:
        res = requests.post(
            url,
            headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
            json=payload,
            timeout=30,
        )
        res.raise_for_status()
        text = res.text.strip()
        # Handle streaming responses
        if "data:" in text:
            for line in text.splitlines():
                if line.startswith("data:"):
                    payload_text = line.removeprefix("data:").strip()
                    try:
                        return json.loads(payload_text)
                    except json.JSONDecodeError:
                        return {"result": payload_text}
        # Handle regular JSON responses
        return res.json()
    except requests.exceptions.RequestException as e:
        return {"error": f"MCP server request to {url} failed: {e}"}

def list_mcp_tools(server_url: str):
    """Fetches available tools from a specific MCP server."""
    # This is a simplified version of call_mcp_server for tool listing
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    try:
        res = requests.post(server_url, json=payload, timeout=15)
        res.raise_for_status()
        resp = res.json()
        result = resp.get("result", {})
        return result.get("tools", []) if isinstance(result, dict) else result
    except requests.exceptions.RequestException:
        return []

def call_tool(name: str, arguments: dict):
    return call_mcp_server("tools/call", {"name": name, "arguments": arguments})

def ask_gemini(prompt: str):
    """Generic call to Gemini for summarization or fallback answers."""
    if not GEMINI_AVAILABLE:
        return "Gemini not configured. Cannot process the request."
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Gemini error: {e}"

# ---------------- GEMINI DECISION LOGIC ----------------
def ask_gemini_for_server_and_tool(query: str):
    """Uses Gemini to decide which server and tool to use for a given query."""
    if not GEMINI_AVAILABLE:
        return {"server": None, "tool": None, "args": None, "explanation": "Gemini not available"}

    # Step 1: Decide which server to use
    server_list_str = "\n".join([f"- {s['name']}: {s['description']}" for s in servers])
    server_prompt = f"""
Analyze the user's query and choose the most appropriate MCP server from the list below.

User query: "{query}"

Available Servers:
{server_list_str}

Respond with only a JSON object containing "server" and "explanation".
Example: {{"server": "kubernetes-mcp", "explanation": "The query is about Kubernetes pods."}}
"""
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(server_prompt)
        parsed_server = extract_json_from_string(response.text)

        if not parsed_server or "server" not in parsed_server or parsed_server["server"] not in server_map:
            return {"server": None, "tool": None, "args": None, "explanation": "Could not decide which server to use."}
        
        server_name = parsed_server["server"]
        explanation1 = parsed_server.get("explanation", "No explanation provided.")

        # Step 2: Fetch tools from the chosen server and decide which tool to use
        tools = list_mcp_tools(server_map[server_name])
        if not tools:
            return {"server": server_name, "tool": None, "args": None, "explanation": f"Chosen server '{server_name}' but it has no available tools or failed to connect."}
        
        tool_names = [t["name"] for t in tools]
        tool_prompt = f"""
Analyze the user's query for the chosen server '{server_name}'. Pick the best tool and determine the required arguments.

User query: "{query}"
Available tools: {json.dumps(tool_names, indent=2)}

Respond with only a JSON object containing "tool", "args", and "explanation".
Example: {{"tool": "list_pods", "args": {{"namespace": "default"}}, "explanation": "The user wants to list pods."}}
"""
        response2 = model.generate_content(tool_prompt)
        parsed_tool = extract_json_from_string(response2.text)

        if not parsed_tool or "tool" not in parsed_tool:
            return {"server": server_name, "tool": None, "args": None, "explanation": "Could not decide which tool to use."}

        return {
            "server": server_name,
            "tool": parsed_tool.get("tool"),
            "args": parsed_tool.get("args") or {},
            "explanation": f"{explanation1} → {parsed_tool.get('explanation', 'No tool explanation.')}"
        }
    except Exception as e:
        return {"server": None, "tool": None, "args": None, "explanation": f"Gemini decision error: {e}"}


# ---------------- STREAMLIT APP ----------------
def main():
    st.set_page_config(page_title="MCP Chat Assistant", page_icon="🤖", layout="wide")
    st.title("🤖 Masa Bot Assistant")

    st.sidebar.subheader("🌐 Available MCP Servers")
    for s in servers:
        st.sidebar.write(f"**{s['name']}** → `{s['url']}`")

    if "messages" not in st.session_state:
        st.session_state.messages = []

    # Display existing messages
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # New chat input, which stays at the bottom of the screen
    if prompt := st.chat_input("Ask about Kubernetes, ArgoCD, Jenkins... 🚀"):
        # Add user message to state and display it
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        # Start processing with the assistant
        with st.chat_message("assistant"):
            # Step 1: Let Gemini decide the server and tool
            with st.spinner("Thinking..."):
                decision = ask_gemini_for_server_and_tool(prompt)
                server = decision.get("server")
                tool = decision.get("tool")
                args = decision.get("args")
                explanation = f"💡 {decision.get('explanation', 'No explanation available.')}"
            
            # Display the decision-making process
            st.markdown(explanation)
            st.session_state.messages.append({"role": "assistant", "content": explanation})

            # Step 2: If a tool was chosen, execute it
            if server and tool:
                # Update the current server URL based on the decision
                st.session_state["current_server"] = server_map[server]
                
                exec_message = f"▶️ Routing to **{server}** and executing **`{tool}`**..."
                st.markdown(exec_message)
                
                with st.spinner(f"Running `{tool}` on `{server}`..."):
                    response = call_tool(tool, args)
                
                # Use a collapsible expander for the raw JSON response
                with st.expander("View Raw JSON Response"):
                    st.json(response)

                # Step 3: Use Gemini to create a user-friendly summary of the result
                with st.spinner("Summarizing the result..."):
                    summary_prompt = f"""
The user asked: "{prompt}"
The tool '{tool}' returned the following JSON data:
{json.dumps(response, indent=2)}

Please provide a clear, human-friendly summary of this data. Use markdown and bullet points for readability. If there's an error in the JSON, explain the error clearly.
"""
                    pretty_answer = ask_gemini(summary_prompt)
                
                st.markdown(pretty_answer)
                st.session_state.messages.append({"role": "assistant", "content": pretty_answer})

            # Fallback: If no tool was chosen, just ask Gemini for a general answer
            else:
                with st.spinner("No specific tool found. Asking Gemini for a general response..."):
                    answer = ask_gemini(prompt)
                st.markdown(answer)
                st.session_state.messages.append({"role": "assistant", "content": answer})

if __name__ == "__main__":
    main()
