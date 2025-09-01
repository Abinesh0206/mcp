import streamlit as st
import requests
import json
import re

# --------------------
# Config
# --------------------
MCP_SERVER_URL = "http://18.234.91.216:3000/mcp"
GEMINI_API_KEY = "AIzaSyC7iRO4NnyQz144aEc6RiVUNzjL9C051V8"
GEMINI_MODEL = "gemini-1.5-flash"

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream"
}

# --------------------
# Helpers
# --------------------
def mcp_request(payload):
    try:
        # For MCP, we need to handle Server-Sent Events (SSE)
        response = requests.post(
            MCP_SERVER_URL, 
            json=payload, 
            headers=HEADERS, 
            timeout=15,
            stream=True
        )
        
        if response.status_code != 200:
            return {"error": f"Status {response.status_code}", "body": response.text}
        
        # Parse SSE response
        full_response = ""
        for line in response.iter_lines():
            if line:
                decoded_line = line.decode('utf-8')
                if decoded_line.startswith('data:'):
                    json_data = decoded_line[5:].strip()
                    if json_data:
                        try:
                            parsed_data = json.loads(json_data)
                            return parsed_data
                        except json.JSONDecodeError:
                            full_response += json_data + "\n"
        
        # If we didn't get proper JSON, return the raw response
        if full_response:
            return {"raw_response": full_response}
        else:
            return {"error": "Empty response from MCP server"}
            
    except Exception as e:
        return {"error": str(e)}

def gemini_request(prompt: str):
    try:
        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}",
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=15
        )
        if response.status_code == 200:
            return response.json()
        return {"error": f"Status {response.status_code}", "body": response.text}
    except Exception as e:
        return {"error": str(e)}

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

# --------------------
# UI Layout
# --------------------
st.set_page_config(page_title="MasaBot", page_icon="🤖", layout="centered")
st.title("🤖 MasaBot – MCP + Gemini UI")

st.markdown("### 🔗 Connected to MCP server")
st.write(f"**MCP URL:** {MCP_SERVER_URL}")

# --------------------
# User Input
# --------------------
user_input = st.text_input("💬 Ask something (Kubernetes / General):")

if st.button("Send") and user_input:
    # First, ask MCP which tools exist (using correct MCP method)
    tools_payload = {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "tools/list",
        "params": {}
    }
    tools_response = mcp_request(tools_payload)
    
    # Try calling a tool if available
    mcp_output = {"error": "No suitable tool found"}
    mcp_success = False
    
    # Check if we got a valid tools list
    if "result" in tools_response and "tools" in tools_response["result"]:
        tools = tools_response["result"]["tools"]
        
        # Parse the user query to determine which tool to use
        tool_name, tool_args = parse_kubernetes_query(user_input)
        
        # If we found a matching tool, try to call it
        if tool_name:
            # Check if the tool exists in available tools
            tool_exists = any(tool["name"] == tool_name for tool in tools)
            
            if tool_exists:
                call_payload = {
                    "jsonrpc": "2.0",
                    "id": "2",
                    "method": "tools/call",
                    "params": {
                        "name": tool_name,
                        "arguments": tool_args
                    }
                }
                st.write(f"🔧 Using MCP tool: **{tool_name}**")
                mcp_output = mcp_request(call_payload)
                
                # Check if MCP call was successful
                if "result" in mcp_output and "content" in mcp_output["result"]:
                    mcp_success = True
                    st.success("✅ MCP Server successfully executed the command!")
                else:
                    st.error("❌ MCP Server returned an error")
            else:
                mcp_output = {"error": f"Tool '{tool_name}' not found in available tools"}
        else:
            mcp_output = {"error": "Could not parse your query into a Kubernetes command"}
    else:
        mcp_output = {"error": "Could not retrieve tools list from MCP server"}

    # Gemini call - only if MCP didn't succeed or we need additional explanation
    gemini_output = gemini_request(user_input)

    # --------------------
    # Display results
    # --------------------
    st.subheader("📡 MCP Server - Tools List")
    st.json(tools_response)

    st.subheader("📡 MCP Server - Tool Call Response")
    
    # Display MCP output in a more readable format if it's successful
    if mcp_success and "result" in mcp_output and "content" in mcp_output["result"]:
        mcp_content = mcp_output["result"]["content"]
        
        # Try to parse JSON output for better display
        if isinstance(mcp_content, list) and len(mcp_content) > 0:
            content_text = mcp_content[0].get("text", "")
            
            # Try to parse as JSON for better formatting
            try:
                parsed_json = json.loads(content_text)
                st.json(parsed_json)
                
                # If it's a list of pods, count them
                if isinstance(parsed_json, dict) and "items" in parsed_json:
                    pod_count = len(parsed_json["items"])
                    st.info(f"📊 Found {pod_count} pods")
                    
            except json.JSONDecodeError:
                # If not JSON, display as text
                st.text_area("MCP Response", content_text, height=200)
        else:
            st.json(mcp_output)
    else:
        st.json(mcp_output)

    st.subheader("🌐 Gemini AI Response")
    
    # Extract and display the text response from Gemini
    if "candidates" in gemini_output and len(gemini_output["candidates"]) > 0:
        gemini_text = gemini_output["candidates"][0]["content"]["parts"][0]["text"]
        
        # If MCP was successful, show Gemini response as additional info
        if mcp_success:
            st.info("💡 Additional information from Gemini:")
            st.markdown(gemini_text)
        else:
            # If MCP failed, show Gemini response as primary answer
            st.warning("⚠️  MCP couldn't process your request, but here's information from Gemini:")
            st.markdown(gemini_text)
    else:
        st.json(gemini_output)

# Display connection status and tools in sidebar
st.sidebar.markdown("## 🔗 Connection Status")
if st.sidebar.button("Test MCP Connection"):
    try:
        ping_payload = {
            "jsonrpc": "2.0",
            "id": "ping-test",
            "method": "ping",
            "params": {}
        }
        response = mcp_request(ping_payload)
        if "result" in response:
            st.sidebar.success("✅ MCP Server is connected and responsive!")
        else:
            st.sidebar.error(f"❌ MCP Server error: {response.get('error', 'Unknown error')}")
    except Exception as e:
        st.sidebar.error(f"❌ Cannot connect to MCP Server: {str(e)}")

# Display available tools
if st.sidebar.button("Show Available Tools"):
    tools_payload = {
        "jsonrpc": "2.0",
        "id": "tools-list",
        "method": "tools/list",
        "params": {}
    }
    tools_response = mcp_request(tools_payload)
    if "result" in tools_response and "tools" in tools_response["result"]:
        st.sidebar.markdown("## 🛠️ Available Tools")
        for tool in tools_response["result"]["tools"]:
            st.sidebar.write(f"**{tool['name']}**: {tool['description']}")
    else:
        st.sidebar.error("Could not retrieve tools list")

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
