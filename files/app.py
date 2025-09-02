import requests
import json
import os

# ---------------- CONFIG ----------------
MCP_SERVER_URL = "http://18.234.91.216:3000"
GEMINI_API_KEY = "AIzaSyA-iOGmYUxW000Nk6ORFFopi3cJE7J8wA4"
GEMINI_MODEL = "gemini-1.5-flash"

# Google Gemini API endpoint
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"

def query_mcp_server(target: str, query: str):
    """
    Sends a query to the MCP server (your Kubernetes MCP server).
    """
    try:
        payload = {
            "target": target,   # e.g. "kubernetes"
            "query": query      # e.g. "get namespaces"
        }
        response = requests.post(f"{MCP_SERVER_URL}/mcp", json=payload, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {"error": str(e)}

def ask_gemini(prompt: str):
    """
    Sends the query/response to Gemini for interpretation and response.
    """
    try:
        headers = {"Content-Type": "application/json"}
        data = {
            "contents": [
                {"parts": [{"text": prompt}]}
            ]
        }
        response = requests.post(GEMINI_URL, headers=headers, json=data, timeout=30)
        response.raise_for_status()
        result = response.json()
        return result["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        return f"❌ Gemini Error: {str(e)}"

def chatbot():
    """
    Interactive chatbot loop
    """
    print("🤖 MasaBot – MCP + Gemini Chatbot")
    print("Connected to MCP server:", MCP_SERVER_URL)
    print("Type 'exit' to quit.\n")

    while True:
        user_input = input("💬 You: ")
        if user_input.lower() in ["exit", "quit"]:
            print("👋 Goodbye!")
            break

        # 1️⃣ Send user query to MCP server
        mcp_response = query_mcp_server("kubernetes", user_input)

        # 2️⃣ Forward MCP response to Gemini
        gemini_prompt = f"User asked: {user_input}\nMCP Server Response: {json.dumps(mcp_response, indent=2)}\n\nExplain or answer in simple terms."
        gemini_answer = ask_gemini(gemini_prompt)

        # 3️⃣ Show response
        print(f"\n📡 MCP Response: {mcp_response}\n")
        print(f"🤖 Gemini: {gemini_answer}\n")

if __name__ == "__main__":
    chatbot()
