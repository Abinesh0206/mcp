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
</style>
""", unsafe_allow_html=True)

# MCP Server Configuration
MCP_SERVER_URL = "http://18.234.91.216:3000/mcp"
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
            st.error(f"Request failed: {e}")
            raise
        except json.JSONDecodeError as e:
            st.error(f"JSON decode failed: {e}")
            raise
    
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
        """Check server health"""
        try:
            response = self._make_request("/health")
            return response.get("status") == "healthy"
        except:
            return False

def ask_gemini(question: str, context: str = "") -> str:
    """Ask Gemini a question with optional context"""
    if not gemini_available:
        return "Gemini API is not available. Please check your API key."
    
    try:
        prompt = f"""
        Context: {context}
        
        Question: {question}
        
        Please provide a helpful response based on the context above.
        If the context is about Kubernetes, provide Kubernetes-specific insights.
        """
        
        response = gemini_model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Error querying Gemini: {e}"

def main():
    """Main Streamlit application"""
    
    # Header
    st.markdown('<h1 class="main-header">🤖 Masabot Kubernetes Assistant</h1>', unsafe_allow_html=True)
    st.markdown("---")
    
    # Initialize client
    client = MCPClient()
    
    # Sidebar
    with st.sidebar:
        st.markdown('<h2 class="sub-header">Masabot Controls</h2>', unsafe_allow_html=True)
        
        # Server status
        if client.health_check():
            st.markdown('<div class="success-box">✅ MCP Server is Online</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="info-box">❌ MCP Server is Offline</div>', unsafe_allow_html=True)
            st.error("Cannot connect to MCP server. Please check if it's running.")
            return
        
        # Namespace selection
        namespaces_data = client.get_namespaces()
        namespaces = [ns['metadata']['name'] for ns in namespaces_data.get('items', [])]
        selected_namespace = st.selectbox("Select Namespace", namespaces)
        
        # Resource type selection
        resource_types = ["Pods", "Services", "Deployments", "ConfigMaps", "Nodes"]
        selected_resource = st.selectbox("Select Resource Type", resource_types)
        
        st.markdown("---")
        st.markdown("### Ask Masabot")
        user_question = st.text_input("Ask a question about your Kubernetes cluster:")
        
        if user_question:
            with st.spinner("Masabot is thinking..."):
                # Get some context from the cluster
                context = f"""
                Kubernetes cluster information:
                - Namespaces: {', '.join(namespaces)}
                - Selected namespace: {selected_namespace}
                - Selected resource: {selected_resource}
                """
                
                answer = ask_gemini(user_question, context)
                st.info(f"**Masabot says:** {answer}")
    
    # Main content area
    tab1, tab2, tab3 = st.tabs(["Cluster Overview", "Resource Details", "Pod Logs"])
    
    with tab1:
        st.markdown('<h2 class="sub-header">Cluster Overview</h2>', unsafe_allow_html=True)
        
        col1, col2, col3 = st.columns(3)
        
        with col1:
            st.markdown("##### Nodes")
            nodes = client.get_nodes()
            st.json(nodes)
        
        with col2:
            st.markdown("##### Namespaces")
            for ns in namespaces:
                st.write(f"- {ns}")
        
        with col3:
            st.markdown("##### Server Info")
            server_info = client.get_server_info()
            st.json(server_info)
    
    with tab2:
        st.markdown('<h2 class="sub-header">Resource Details</h2>', unsafe_allow_html=True)
        
        if selected_resource == "Pods":
            pods = client.get_pods(selected_namespace)
            for pod in pods.get('items', []):
                with st.expander(f"Pod: {pod['metadata']['name']}"):
                    st.json(pod)
        
        elif selected_resource == "Services":
            services = client.get_services(selected_namespace)
            for service in services.get('items', []):
                with st.expander(f"Service: {service['metadata']['name']}"):
                    st.json(service)
        
        elif selected_resource == "Deployments":
            deployments = client.get_deployments(selected_namespace)
            for deployment in deployments.get('items', []):
                with st.expander(f"Deployment: {deployment['metadata']['name']}"):
                    st.json(deployment)
        
        elif selected_resource == "ConfigMaps":
            config_maps = client.get_config_maps(selected_namespace)
            for cm in config_maps.get('items', []):
                with st.expander(f"ConfigMap: {cm['metadata']['name']}"):
                    st.json(cm)
        
        elif selected_resource == "Nodes":
            nodes = client.get_nodes()
            for node in nodes.get('items', []):
                with st.expander(f"Node: {node['metadata']['name']}"):
                    st.json(node)
    
    with tab3:
        st.markdown('<h2 class="sub-header">Pod Logs Viewer</h2>', unsafe_allow_html=True)
        
        pods = client.get_pods(selected_namespace)
        pod_names = [pod['metadata']['name'] for pod in pods.get('items', [])]
        
        if pod_names:
            selected_pod = st.selectbox("Select Pod", pod_names)
            log_lines = st.slider("Number of log lines", 10, 1000, 100)
            
            if st.button("Fetch Logs"):
                with st.spinner("Fetching logs..."):
                    logs = client.get_pod_logs(selected_pod, selected_namespace, log_lines)
                    st.text_area("Logs", logs.get('logs', ''), height=400)
        else:
            st.info("No pods found in the selected namespace.")
    
    # Footer
    st.markdown("---")
    st.markdown('<div class="footer">Masabot Kubernetes Assistant • Powered by MCP Server and Gemini</div>', unsafe_allow_html=True)

if __name__ == "__main__":
    main()
