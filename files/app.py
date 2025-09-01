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

    # 🔥 Count namespaces queries
    elif "how many namespaces" in query_lower or "count namespaces" in query_lower:
        return "kubectl_get", {"resourceType": "namespaces", "output": "json"}

    # List namespaces
    elif "get namespaces" in query_lower or "list namespaces" in query_lower or "show namespaces" in query_lower:
        return "kubectl_get", {"resourceType": "namespaces", "output": "json"}

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
    tools_payload = {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "tools/list",
        "params": {}
    }
    tools_response = mcp_request(tools_payload)

    mcp_output = {"error": "No suitable tool found"}
    mcp_success = False

    if "result" in tools_response and "tools" in tools_response["result"]:
        tools = tools_response["result"]["tools"]

        tool_name, tool_args = parse_kubernetes_query(user_input)

        if tool_name:
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

    gemini_output = gemini_request(user_input)

    st.subheader("📡 MCP Server - Tools List")
    st.json(tools_response)

    st.subheader("📡 MCP Server - Tool Call Response")

    if mcp_success and "result" in mcp_output and "content" in mcp_output["result"]:
        mcp_content = mcp_output["result"]["content"]

        if isinstance(mcp_content, list) and len(mcp_content) > 0:
            content_text = mcp_content[0].get("text", "")

            try:
                parsed_json = json.loads(content_text)
                st.json(parsed_json)

                if isinstance(parsed_json, dict) and "items" in parsed_json:
                    count = len(parsed_json["items"])
                    st.info(f"📊 Found {count} items")
            except json.JSONDecodeError:
                st.text_area("MCP Response", content_text, height=200)
        else:
            st.json(mcp_output)
    else:
        st.json(mcp_output)

    st.subheader("🌐 Gemini AI Response")

    if "candidates" in gemini_output and len(gemini_output["candidates"]) > 0:
        gemini_text = gemini_output["candidates"][0]["content"]["parts"][0]["text"]

        if mcp_success:
            st.info("💡 Additional information from Gemini:")
            st.markdown(gemini_text)
        else:
            st.warning("⚠️ MCP couldn't process your request, but here's information from Gemini:")
            st.markdown(gemini_text)
    else:
        st.json(gemini_output)

# --------------------
# Sidebar
# --------------------
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

st.sidebar.markdown("## 💡 Example Queries")
examples = [
    "How many namespaces in my cluster?",
    "Get namespaces",
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
