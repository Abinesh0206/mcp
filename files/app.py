#!/usr/bin/env python3
"""
MCP Client with Streamlit UI for Masabot
This client connects to the MCP server running at 18.234.91.216:3000/mcp
"""

import streamlit as st
import requests
import json
import time
from typing import Optional, Dict, Any, List
import google.generativeai as genai

# Configure the page
st.set_page_config(
    page_title="Masabot - Kubernetes MCP Client",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Apply custom CSS for blue and orange theme
st.markdown("""
<style>
    .main-header {
        font-size: 3rem;
        color: #1E88E5;
        text-align: center;
    }
    .sub-header {
        font-size: 1.5rem;
        color: #FF9800;
        border-bottom: 2px solid #FF9800;
        padding-bottom: 0.5rem;
    }
    .success-box {
        background-color: #E3F2FD;
        padding: 1rem;
        border-radius: 0.5rem;
        border-left: 4px solid #1E88E5;
    }
    .info-box {
        background-color: #FFF3E0;
        padding: 1rem;
        border-radius: 0.5rem;
        border-left: 4px solid #FF9800;
    }
    .stButton button {
        background-color: #1E88E5;
        color: white;
    }
    .stButton button:hover {
        background-color: #FF9800;
        color: white;
    }
    .footer {
        text-align: center;
        padding: 1rem;
        color: #757575;
        font-size: 0.8rem;
    }
    .chat-message {
        padding: 1rem;
        border-radius: 0.5rem;
        margin-bottom: 1rem;
    }
    .user-message {
        background-color: #E3F2FD;
        border-left: 4px solid #1E88E5;
    }
    .bot-message {
        background-color: #FFF3E0;
        border-left: 4px solid #FF9800;
    }
</style>
""", unsafe_allow_html=True)

# MCP Server Configuration
MCP_SERVER_URL = "http://18.234.91.216:3000"
GEMINI_API_KEY = "AIzaSyA-iOGmYUxW000Nk6ORFFopi3cJE7J8wA4"
GEMINI_MODEL = "gemini-1.5-flash"

# Initialize Gemini
try:
    genai.configure(api_key=GEMINI_API_KEY)
    gemini_model = genai.GenerativeModel(GEMINI_MODEL)
    gemini_available = True
except Exception as e:
    st.warning(f"Gemini API not available: {e}")
    gemini_available = False

class MCPClient:
    """MCP Client for interacting with Kubernetes MCP Server"""
    
    def __init__(self, base_url: str = MCP_SERVER_URL):
        self.base_url = base_url
        self.session = requests.Session()
    
    def _make_request(self, endpoint: str, method: str = "GET", 
                     data: Optional[Dict] = None) -> Dict[str, Any]:
        """Make HTTP request to MCP server"""
        url = f"{self.base_url}{endpoint}"
        
        try:
            if method.upper() == "GET":
                response = self.session.get(url)
            elif method.upper() == "POST":
                response = self.session.post(url, json=data)
            else:
                raise ValueError(f"Unsupported method: {method}")
            
            response.raise_for_status()
            return response.json()
            
        except requests.RequestException as e:
            st.error(f"Request to {endpoint} failed: {e}")
            return {"error": str(e)}
        except json.JSONDecodeError as e:
            st.error(f"JSON decode failed: {e}")
            return {"error": "Invalid JSON response"}
    
    def get_server_info(self) -> Dict[str, Any]:
        """Get server information"""
        return self._make_request("/api/info")
    
    def get_pods(self, namespace: Optional[str] = None) -> Dict[str, Any]:
        """Get pods information"""
        endpoint = "/api/pods"
        if namespace:
            endpoint += f"?namespace={namespace}"
        return self._make_request(endpoint)
    
    def get_pod_logs(self, pod_name: str, namespace: Optional[str] = None, 
                    tail_lines: int = 100) -> Dict[str, Any]:
        """Get pod logs"""
        endpoint = f"/api/pods/{pod_name}/logs?tail={tail_lines}"
        if namespace:
            endpoint += f"&namespace={namespace}"
        return self._make_request(endpoint)
    
    def get_nodes(self) -> Dict[str, Any]:
        """Get nodes information"""
        return self._make_request("/api/nodes")
    
    def get_services(self, namespace: Optional[str] = None) -> Dict[str, Any]:
        """Get services information"""
        endpoint = "/api/services"
        if namespace:
            endpoint += f"?namespace={namespace}"
        return self._make_request(endpoint)
    
    def get_deployments(self, namespace: Optional[str] = None) -> Dict[str, Any]:
        """Get deployments information"""
        endpoint = "/api/deployments"
        if namespace:
            endpoint += f"?namespace={namespace}"
        return self._make_request(endpoint)
    
    def get_config_maps(self, namespace: Optional[str] = None) -> Dict[str, Any]:
        """Get config maps information"""
        endpoint = "/api/configmaps"
        if namespace:
            endpoint += f"?namespace={namespace}"
        return self._make_request(endpoint)
    
    def get_namespaces(self) -> Dict[str, Any]:
        """Get all namespaces"""
        return self._make_request("/api/namespaces")
    
    def health_check(self) -> bool:
        """Check server health by trying to access a basic endpoint"""
        try:
            # Try to get namespaces as a health check
            response = self._make_request("/api/namespaces")
            return "items" in response
        except:
            return False

def ask_gemini(question: str, context: str = "") -> str:
    """Ask Gemini a question with optional context"""
    if not gemini_available:
        return "Gemini API is not available. Please check your API key."
    
    try:
        prompt = f"""
        You are Masabot, a Kubernetes cluster assistant. 
        Context: {context}
        
        Question: {question}
        
        Please provide a helpful response based on the context above.
        If the context is about Kubernetes, provide Kubernetes-specific insights.
        Be concise but informative.
        """
        
        response = gemini_model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Error querying Gemini: {e}"

def main():
    """Main Streamlit application"""
    
    # Initialize session state for chat
    if "messages" not in st.session_state:
        st.session_state.messages = []
    
    # Header
    st.markdown('<h1 class="main-header">🤖 Masabot Kubernetes Assistant</h1>', unsafe_allow_html=True)
    st.markdown("---")
    
    # Initialize client
    client = MCPClient()
    
    # Sidebar
    with st.sidebar:
        st.markdown('<h2 class="sub-header">Masabot Controls</h2>', unsafe_allow_html=True)
        
        # Server status - use a more robust health check
        try:
            # Try to get a simple endpoint to check if server is responsive
            test_response = client._make_request("/api/namespaces")
            if "error" not in test_response:
                st.markdown('<div class="success-box">✅ MCP Server is Online</div>', unsafe_allow_html=True)
                server_online = True
            else:
                st.markdown('<div class="info-box">❌ MCP Server Error</div>', unsafe_allow_html=True)
                st.error(f"Server error: {test_response.get('error', 'Unknown error')}")
                server_online = False
        except Exception as e:
            st.markdown('<div class="info-box">❌ MCP Server is Offline</div>', unsafe_allow_html=True)
            st.error(f"Cannot connect to MCP server: {e}")
            server_online = False
        
        if not server_online:
            return
        
        # Namespace selection
        try:
            namespaces_data = client.get_namespaces()
            if "items" in namespaces_data:
                namespaces = [ns['metadata']['name'] for ns in namespaces_data.get('items', [])]
                selected_namespace = st.selectbox("Select Namespace", namespaces)
            else:
                st.error("Could not fetch namespaces")
                namespaces = ["default"]
                selected_namespace = "default"
        except:
            namespaces = ["default"]
            selected_namespace = "default"
        
        # Resource type selection
        resource_types = ["Pods", "Services", "Deployments", "ConfigMaps", "Nodes"]
        selected_resource = st.selectbox("Select Resource Type", resource_types)
        
        st.markdown("---")
        st.markdown("### Quick Actions")
        
        if st.button("🔄 Refresh Cluster Data"):
            st.rerun()
    
    # Main content area with tabs
    tab1, tab2, tab3, tab4 = st.tabs(["Chat with Masabot", "Cluster Overview", "Resource Details", "Pod Logs"])
    
    # Chat tab
    with tab1:
        st.markdown('<h2 class="sub-header">Chat with Masabot</h2>', unsafe_allow_html=True)
        
        # Display chat messages
        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])
        
        # Chat input
        if prompt := st.chat_input("Ask Masabot about your Kubernetes cluster..."):
            # Add user message to chat history
            st.session_state.messages.append({"role": "user", "content": prompt})
            
            # Display user message
            with st.chat_message("user"):
                st.markdown(prompt)
            
            # Get cluster context for Gemini
            try:
                context = f"""
                Kubernetes cluster information:
                - Available namespaces: {', '.join(namespaces) if 'namespaces' in locals() else 'Unknown'}
                - Selected namespace: {selected_namespace if 'selected_namespace' in locals() else 'Unknown'}
                """
            except:
                context = "Kubernetes cluster context not available"
            
            # Display assistant response
            with st.chat_message("assistant"):
                with st.spinner("Masabot is thinking..."):
                    response = ask_gemini(prompt, context)
                    st.markdown(response)
            
            # Add assistant response to chat history
            st.session_state.messages.append({"role": "assistant", "content": response})
    
    # Cluster Overview tab
    with tab2:
        st.markdown('<h2 class="sub-header">Cluster Overview</h2>', unsafe_allow_html=True)
        
        col1, col2, col3 = st.columns(3)
        
        with col1:
            st.markdown("##### Nodes")
            nodes = client.get_nodes()
            if "items" in nodes:
                st.write(f"Total Nodes: {len(nodes.get('items', []))}")
                for node in nodes.get('items', [])[:3]:  # Show first 3 nodes
                    st.write(f"- {node['metadata']['name']}")
                if len(nodes.get('items', [])) > 3:
                    st.write(f"... and {len(nodes.get('items', [])) - 3} more")
            else:
                st.error("Could not fetch nodes")
        
        with col2:
            st.markdown("##### Namespaces")
            if "namespaces" in locals():
                st.write(f"Total Namespaces: {len(namespaces)}")
                for ns in namespaces[:5]:  # Show first 5 namespaces
                    st.write(f"- {ns}")
                if len(namespaces) > 5:
                    st.write(f"... and {len(namespaces) - 5} more")
            else:
                st.error("Could not fetch namespaces")
        
        with col3:
            st.markdown("##### Server Info")
            server_info = client.get_server_info()
            if "error" not in server_info:
                st.json(server_info)
            else:
                st.error("Could not fetch server info")
    
    # Resource Details tab
    with tab3:
        st.markdown('<h2 class="sub-header">Resource Details</h2>', unsafe_allow_html=True)
        
        if selected_resource == "Pods":
            pods = client.get_pods(selected_namespace)
            if "items" in pods:
                st.write(f"Found {len(pods.get('items', []))} pods in {selected_namespace}")
                for pod in pods.get('items', []):
                    with st.expander(f"Pod: {pod['metadata']['name']}"):
                        st.json(pod)
            else:
                st.error("Could not fetch pods")
        
        elif selected_resource == "Services":
            services = client.get_services(selected_namespace)
            if "items" in services:
                st.write(f"Found {len(services.get('items', []))} services in {selected_namespace}")
                for service in services.get('items', []):
                    with st.expander(f"Service: {service['metadata']['name']}"):
                        st.json(service)
            else:
                st.error("Could not fetch services")
        
        elif selected_resource == "Deployments":
            deployments = client.get_deployments(selected_namespace)
            if "items" in deployments:
                st.write(f"Found {len(deployments.get('items', []))} deployments in {selected_namespace}")
                for deployment in deployments.get('items', []):
                    with st.expander(f"Deployment: {deployment['metadata']['name']}"):
                        st.json(deployment)
            else:
                st.error("Could not fetch deployments")
        
        elif selected_resource == "ConfigMaps":
            config_maps = client.get_config_maps(selected_namespace)
            if "items" in config_maps:
                st.write(f"Found {len(config_maps.get('items', []))} config maps in {selected_namespace}")
                for cm in config_maps.get('items', []):
                    with st.expander(f"ConfigMap: {cm['metadata']['name']}"):
                        st.json(cm)
            else:
                st.error("Could not fetch config maps")
        
        elif selected_resource == "Nodes":
            nodes = client.get_nodes()
            if "items" in nodes:
                st.write(f"Found {len(nodes.get('items', []))} nodes")
                for node in nodes.get('items', []):
                    with st.expander(f"Node: {node['metadata']['name']}"):
                        st.json(node)
            else:
                st.error("Could not fetch nodes")
    
    # Pod Logs tab
    with tab4:
        st.markdown('<h2 class="sub-header">Pod Logs Viewer</h2>', unsafe_allow_html=True)
        
        pods = client.get_pods(selected_namespace)
        if "items" in pods and pods.get('items', []):
            pod_names = [pod['metadata']['name'] for pod in pods.get('items', [])]
            
            selected_pod = st.selectbox("Select Pod", pod_names)
            log_lines = st.slider("Number of log lines", 10, 1000, 100)
            
            if st.button("Fetch Logs"):
                with st.spinner("Fetching logs..."):
                    logs = client.get_pod_logs(selected_pod, selected_namespace, log_lines)
                    if "logs" in logs:
                        st.text_area("Logs", logs.get('logs', ''), height=400)
                    else:
                        st.error("Could not fetch logs")
        else:
            st.info("No pods found in the selected namespace.")
    
    # Footer
    st.markdown("---")
    st.markdown('<div class="footer">Masabot Kubernetes Assistant • Powered by MCP Server and Gemini</div>', unsafe_allow_html=True)

if __name__ == "__main__":
    main()
