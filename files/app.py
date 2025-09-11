import os
import json
import requests
import streamlit as st
from dotenv import load_dotenv
import google.generativeai as genai
from datetime import datetime, timezone

# ---------------- CONFIG ----------------
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyA-iOGmYUxW000Nk6ORFFopi3cJE7J8wA4")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

genai.configure(api_key=GEMINI_API_KEY)

# ---------------- LOAD SERVERS ----------------
def load_servers():
    try:
        with open("servers.json") as f:
            data = json.load(f)
            return data.get("servers", [])
    except Exception as e:
        return [{"name": "default", "url": "http://127.0.0.1:3000/mcp", "description": f"Fallback server: {e}"}]

servers = load_servers()

# Default to first server
if "current_server" not in st.session_state:
    st.session_state["current_server"] = servers[0]["url"]

def get_current_server_url():
    return st.session_state.get("current_server", servers[0]["url"])

# ---------------- HELPERS ----------------
def call_mcp_server(method: str, params: dict = None):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    try:
        res = requests.post(
            get_current_server_url(),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json=payload,
            timeout=30,
        )
        res.raise_for_status()
        text = res.text.strip()
        # handle SSE-like response that MCP sometimes returns
        if text.startswith("event:"):
            for line in text.splitlines():
                if line.startswith("data:"):
                    return json.loads(line[len("data:"):].strip())
        return res.json()
    except requests.exceptions.RequestException as e:
        return {"error": f"MCP server request failed: {str(e)}"}

def list_mcp_tools():
    resp = call_mcp_server("tools/list")
    if "result" in resp and isinstance(resp["result"], dict):
        return resp["result"].get("tools", [])
    return []

def call_tool(name: str, arguments: dict):
    if not name or not isinstance(arguments, dict):
        return {"error": "Invalid tool name or arguments"}
    return call_mcp_server("tools/call", {"name": name, "arguments": arguments})

def humanize_age(created_at: str) -> str:
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - created
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return f"{seconds}s"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes}m"
        hours = minutes // 60
        if hours < 24:
            return f"{hours}h{minutes%60}m"
        days = hours // 24
        hours = hours % 24
        return f"{days}d{hours}h"
    except Exception:
        return "-"

def ask_gemini(prompt: str):
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Gemini error: {str(e)}"

def sanitize_args(args: dict):
    if not args:
        return {}
    fixed = args.copy()
    if "resource" in fixed and "resourceType" not in fixed:
        fixed["resourceType"] = fixed.pop("resource")
    if fixed.get("resourceType") == "pods" and "namespace" not in fixed:
        fixed["namespace"] = "default"
    if fixed.get("namespace") == "all":
        fixed["allNamespaces"] = True
        fixed.pop("namespace", None)
    return fixed

def ask_gemini_for_tool_decision(query: str):
    tools = list_mcp_tools()
    tool_names = [t["name"] for t in tools]

    instruction = f"""
You are an AI agent that maps user queries to MCP tools.

User query: "{query}"

Available tools in this MCP server: {json.dumps(tool_names, indent=2)}

Rules:
- Only choose from the tools above.
- If the query clearly maps to a tool, return tool + args in JSON.
- If unsure, set tool=null and args=null.

Respond ONLY in strict JSON:
{{
  "tool": "<tool_name>" | null,
  "args": {{}} | null,
  "explanation": "Short explanation"
}}
"""
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(instruction)
        text = response.text.strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}") + 1
            parsed = json.loads(text[start:end]) if start != -1 and end != -1 else {"tool": None, "args": None, "explanation": f"Gemini invalid: {text}"}
        parsed["args"] = sanitize_args(parsed.get("args"))
        return parsed
    except Exception as e:
        return {"tool": None, "args": None, "explanation": f"Gemini error: {str(e)}"}

# ---------------- NEW: ArgoCD application helper ----------------
def try_create_argocd_app(args: dict):
    """
    Try multiple likely MCP tool names for creating an ArgoCD Application.
    Returns the first successful response or the last error received.
    """
    possible_tool_names = [
        "argocd/create_application",
        "argocd_create_application",
        "applications/create",
        "argocd.applications.create",
        "argocd.create_application",
        # fallback generic tool which simply sends raw method (some MCP servers map differently)
        # We will also try to call tools/call with name "argocd-create-application"
        "argocd-create-application",
    ]
    last_err = None
    for tname in possible_tool_names:
        resp = call_tool(tname, args)
        # consider success if there is no "error" field and either "result" or a success string
        if isinstance(resp, dict) and not resp.get("error") and (resp.get("result") is not None or resp):
            return {"tool": tname, "response": resp}
        last_err = {"tool": tname, "response": resp}
    return {"tool": None, "response": last_err}

# ---------------- STREAMLIT APP ----------------
def main():
    st.set_page_config(page_title="MCP Chat Assistant", page_icon="⚡", layout="wide")
    st.title("🤖 Masa Bot Assistant")

    # Sidebar: select MCP server
    st.sidebar.subheader("🌐 Select MCP Server")
    server_names = [f"{s['name']} ({s['url']})" for s in servers]
    choice = st.sidebar.radio("Available Servers:", server_names)
    st.session_state["current_server"] = next(s["url"] for s in servers if choice.startswith(s["name"]))

    # Show tools for current server
    tools = list_mcp_tools()
    if tools:
        st.sidebar.subheader("🔧 Available MCP Tools")
        for t in tools:
            st.sidebar.write(f"- {t['name']}: {t.get('description','')}")
    else:
        st.sidebar.error("⚠ Could not fetch tools from MCP server.")

    if "messages" not in st.session_state:
        st.session_state["messages"] = []

    # ------ NEW: ArgoCD Application creation form ------
    st.markdown("## ➕ Create ArgoCD Application (manual form)")
    with st.form("argocd_app_form"):
        st.write("### GENERAL")
        app_name = st.text_input("Application Name", value="")
        project = st.text_input("Project Name", value="default")
        sync_policy = st.selectbox("Sync Policy", options=["Manual", "Automatic"], index=0)
        manual_flag = (sync_policy == "Manual")
        set_deletion_finalizer = st.checkbox("Set Deletion Finalizer", value=False)
        skip_schema_validation = st.checkbox("Skip Schema Validation", value=False)
        auto_create_namespace = st.checkbox("Auto-Create Namespace", value=False)
        prune_last = st.checkbox("Prune Last", value=False)
        apply_out_of_sync_only = st.checkbox("Apply Out of Sync Only", value=False)
        respect_ignore_differences = st.checkbox("Respect Ignore Differences", value=False)
        server_side_apply = st.checkbox("Server-Side Apply", value=False)
        prune_propagation_policy = st.selectbox("Prune Propagation Policy", options=["foreground", "background", "orphan"], index=0)
        replace_flag = st.checkbox("Replace", value=False)
        retry_flag = st.checkbox("Retry", value=False)

        st.write("---")
        st.write("### SOURCE")
        repository_url = st.text_input("Repository URL (GIT)", value="")
        revision = st.text_input("Revision (HEAD)", value="HEAD")
        branches = st.text_input("Branches (comma separated)", value="")
        path = st.text_input("Path (path to manifests, e.g. apps/my-app)", value="")

        st.write("---")
        st.write("### DESTINATION")
        dest_cluster_url = st.text_input("Cluster URL (eg https://kubernetes.default.svc)", value="")
        dest_namespace = st.text_input("Destination Namespace", value="default")

        submitted_app = st.form_submit_button("Create ArgoCD Application")

    if submitted_app:
        # Validate minimal inputs
        if not app_name or not repository_url or not path:
            st.error("Please provide at least Application Name, Repository URL and Path.")
        else:
            # Build args payload
            branches_list = [b.strip() for b in branches.split(",")] if branches else []
            args = {
                "name": app_name,
                "project": project,
                "syncPolicy": "Manual" if manual_flag else "Automatic",
                "settings": {
                    "setDeletionFinalizer": set_deletion_finalizer,
                    "skipSchemaValidation": skip_schema_validation,
                    "autoCreateNamespace": auto_create_namespace,
                    "pruneLast": prune_last,
                    "applyOutOfSyncOnly": apply_out_of_sync_only,
                    "respectIgnoreDifferences": respect_ignore_differences,
                    "serverSideApply": server_side_apply,
                    "prunePropagationPolicy": prune_propagation_policy,
                    "replace": replace_flag,
                    "retry": retry_flag,
                },
                "source": {
                    "repoURL": repository_url,
                    "path": path,
                    "targetRevision": revision,
                },
                "destination": {
                    "server": dest_cluster_url,
                    "namespace": dest_namespace,
                }
            }
            if branches_list:
                args["source"]["branches"] = branches_list

            # sanitize (reuse your helper)
            args = sanitize_args(args)

            st.chat_message("assistant").markdown(f"🔧 Attempting to create ArgoCD Application with args:\n```json\n{json.dumps(args, indent=2)}\n```")

            result = try_create_argocd_app(args)
            if result.get("tool"):
                st.success(f"Called tool `{result['tool']}`. Response:")
                st.write(result["response"])
                st.session_state["messages"].append({"role":"assistant", "content": f"Created via `{result['tool']}`: {json.dumps(result['response'], indent=2)}"})
            else:
                st.error("Failed to create ArgoCD application. Tried multiple tool names; see last responses.")
                st.write(result["response"])
                st.session_state["messages"].append({"role":"assistant", "content": f"Create failed: {json.dumps(result['response'], indent=2)}"})

    # Display chat history
    for msg in st.session_state["messages"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Chat input (original)
    with st.form("user_input_form", clear_on_submit=True):
        user_input = st.text_input("Ask Kubernetes or ArgoCD something...")
        submitted = st.form_submit_button("Send")
        if submitted and user_input:
            st.session_state["messages"].append({"role": "user", "content": user_input})
            st.chat_message("user").markdown(user_input)

            decision = ask_gemini_for_tool_decision(user_input)
            explanation = f"💡 {decision.get('explanation','')}"
            st.session_state["messages"].append({"role": "assistant", "content": explanation})
            st.chat_message("assistant").markdown(explanation)

            if decision["tool"]:
                st.chat_message("assistant").markdown(
                    f"🔧 Executing *{decision['tool']}* with arguments:\n```json\n{json.dumps(decision['args'], indent=2)}\n```"
                )
                response = call_tool(decision['tool'], decision['args'])

                pretty_answer = ask_gemini(
                    f"User asked: {user_input}\n\n"
                    f"Here is the raw MCP response:\n{json.dumps(response, indent=2)}\n\n"
                    f"Answer in natural human-friendly language. "
                    f"If multiple items (pods, apps, projects, services), format as bullet points."
                )

                st.session_state["messages"].append({"role": "assistant", "content": pretty_answer})
                st.chat_message("assistant").markdown(pretty_answer)

            else:
                answer = ask_gemini(user_input)
                st.session_state["messages"].append({"role": "assistant", "content": answer})
                st.chat_message("assistant").markdown(answer)

if __name__ == "__main__":
    main()
