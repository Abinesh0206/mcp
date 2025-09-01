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
    
    # Check if we got a valid tools list
    if "result" in tools_response and "tools" in tools_response["result"]:
        tools = tools_response["result"]["tools"]
        
        # Look for a relevant tool based on the user's query
        tool_to_use = None
        tool_arguments = {}
        
        # Check if user wants to count pods
        if "how many pods" in user_input.lower() or "count pods" in user_input.lower():
            for tool in tools:
                if tool["name"] == "kubectl_get":
                    tool_to_use = tool["name"]
                    tool_arguments = {
                        "resourceType": "pods",
                        "allNamespaces": True,
                        "output": "json"
                    }
                    break
        
        # Check if user wants to create a namespace
        elif "create namespace" in user_input.lower():
            namespace_name = user_input.lower().replace("create namespace", "").strip()
            for tool in tools:
                if tool["name"] == "kubectl_create":
                    tool_to_use = tool["name"]
                    tool_arguments = {
                        "resourceType": "namespace",
                        "name": namespace_name
                    }
                    break
        
        # Check if user wants to get any resource
        elif "get " in user_input.lower():
            # Extract resource type from query
            parts = user_input.lower().split("get ")
            if len(parts) > 1:
                resource_part = parts[1].split()[0]
                for tool in tools:
                    if tool["name"] == "kubectl_get":
                        tool_to_use = tool["name"]
                        tool_arguments = {
                            "resourceType": resource_part,
                            "allNamespaces": True,
                            "output": "json"
                        }
                        break
        
        # If we found a tool, try to call it
        if tool_to_use:
            call_payload = {
                "jsonrpc": "2.0",
                "id": "2",
                "method": "tools/call",
                "params": {
                    "name": tool_to_use,
                    "arguments": tool_arguments
                }
            }
            mcp_output = mcp_request(call_payload)
        else:
            mcp_output = {"error": "No appropriate tool found for this request"}
    else:
        mcp_output = {"error": "Could not retrieve tools list from MCP server"}

    # Gemini call
    gemini_output = gemini_request(user_input)

    # --------------------
    # Display results
    # --------------------
    st.subheader("📡 MCP Server - Tools List")
    st.json(tools_response)

    st.subheader("📡 MCP Server - Tool Call Response")
    st.json(mcp_output)

    st.subheader("🌐 Gemini AI Response")
    
    # Extract and display the text response from Gemini in a more readable format
    if "candidates" in gemini_output and len(gemini_output["candidates"]) > 0:
        gemini_text = gemini_output["candidates"][0]["content"]["parts"][0]["text"]
        st.markdown(gemini_text)
    else:
        st.json(gemini_output)
