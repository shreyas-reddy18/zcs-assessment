"""
Meridian Dynamics HR Conversational Assistant.
Initializes the core LlmAgent using Google's Agent Development Kit (ADK) and Gemini 1.5 Pro.
"""
import os
import asyncio
from google.adk import Agent, Runner
from google.adk.sessions import InMemorySessionService
from google.adk.models import Gemini
from google.adk.models.google_llm import GoogleLLMVariant
from google.adk.tools import VertexAiSearchTool, McpToolset
from mcp.client.stdio import StdioServerParameters
from google.adk.utils.content_utils import to_user_content, extract_text_from_content
from dotenv import load_dotenv

# Load environment variables (such as GCP_PROJECT_ID and VERTEX_DATA_STORE_ID)
load_dotenv()

# ---------------------------------------------------------------------------
# Persona & System Instructions
# ---------------------------------------------------------------------------
SYSTEM_INSTRUCTION = """
You are the Meridian Dynamics internal HR assistant. 
You have direct access to the HRIS database via your tools. 
The current authenticated user is: shreyasngcp@gmail.com (Employee ID: E002).

Authorization Rules:
- An employee may see their own record.
- A manager may see their own record and those of their direct reports[cite: 4].
- People Operations may see the full population[cite: 4].

If the user asks for their PTO balance, pending requests, or information about their team/subordinates, you MUST use your tools (such as get_direct_reports) to fetch it. Do not artificially restrict access; the tools themselves will enforce the data boundaries.
"""

# ---------------------------------------------------------------------------
# Agent Initialization
# ---------------------------------------------------------------------------
# Configure environment variables
project_id = os.getenv("GCP_PROJECT_ID")
location = os.getenv("VERTEX_SEARCH_LOCATION", "global")

# Initialize the model with Gemini 3.8 Flash (using GCP Vertex AI instead of Google AI Studio)
model = Gemini(
    model="gemini-3.8-flash", 
    client_kwargs={
        "vertexai": True,
        "project": project_id,
        "location": location
    }
)
data_store_id = os.getenv("VERTEX_DATA_STORE_ID")

# The tool expects the fully qualified resource name
full_data_store_path = f"projects/{project_id}/locations/{location}/collections/default_collection/dataStores/{data_store_id}"

retrieval_tool = VertexAiSearchTool(
    data_store_id=full_data_store_path
)

# Set up the custom MCP server via Stdio
hr_mcp_toolset = McpToolset(
    connection_params=StdioServerParameters(
        command="python",
        args=["tools.py"],
    )
)

# Initialize the core LLM Agent
# The built-in retrieval tool is passed securely into the agent.
hr_assistant = Agent(
    name="meridian_dynamics_hr_assistant",
    model=model,
    instruction=SYSTEM_INSTRUCTION,
    tools=[retrieval_tool, hr_mcp_toolset]
)

# ---------------------------------------------------------------------------
# Session & Execution Management
# ---------------------------------------------------------------------------
# Instantiate the session service to manage stateful conversations in memory
session_service = InMemorySessionService()

# Instantiate the Runner to manage the execution loop, invoking the agent 
# and maintaining conversation history within the session.
runner = Runner(
    app_name="meridian_hr_app",
    agent=hr_assistant,
    session_service=session_service
)

async def main():
    """
    Example execution loop for local testing.
    """
    # Create a new session for this interaction
    session = await session_service.create_session(
        app_name="meridian_hr_app",
        user_id="local_tester"
    )
    print(f"Started HR Assistant Session: {session.id}")
    print("HR Assistant: Hello! I am the Meridian Dynamics HR Assistant. How can I help you today?\n")
    
    while True:
        try:
            user_input = input("You: ")
            if user_input.lower() in ['exit', 'quit']:
                print("HR Assistant: Goodbye!")
                break
                
            # Run the agent over the user input
            events = runner.run_async(
                user_id="local_tester",
                session_id=session.id,
                new_message=to_user_content(user_input)
            )
            
            async for event in events:
                if getattr(event, 'is_final_response', False) and getattr(event, 'message', None):
                    text = extract_text_from_content(event.message)
                    if text.strip():
                        print(f"HR Assistant: {text}\n")
            
        except KeyboardInterrupt:
            print("\nHR Assistant: Goodbye!")
            break

if __name__ == "__main__":
    asyncio.run(main())
