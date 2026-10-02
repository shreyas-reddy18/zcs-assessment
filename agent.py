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

Authorization Rules:
- An employee may see their own record.
- A manager may see their own record and those of their direct reports.
- People Operations may see the full population.

If the user asks for their PTO balance, pending requests, or information about their team/subordinates, you MUST use your tools (such as get_direct_reports) to fetch it. Do not artificially restrict access; the tools themselves will enforce the data boundaries.

Policy Versioning Rules:
- The company has policies for both 2025 and 2026.
- If the user does not specify a year, you MUST assume the current year is 2026.
- You must actively filter your document retrieval using the `plan_year` metadata field. 
- Never blend answers from two different plan years.

When a user asks to submit a PTO request, you MUST NOT guess the rules. You MUST first use your retrieval tool to search the PTO & Leave Policy for the governing plan year (defaulting to 2026) to determine the required notice period/lead time and any blackout dates. You must extract these exact rules from the document and pass them as arguments into the validate_pto_request tool to evaluate the request.
Format blackout_periods as a list of strings using 'YYYY-MM-DD to YYYY-MM-DD' (e.g., ['2026-12-20 to 2026-12-31']).

You will be provided with the current authenticated user's email in your Security Context. You MUST pass this exact email string into the current_user_email parameter of every tool you call.
"""

# ---------------------------------------------------------------------------
# Agent Initialization
# ---------------------------------------------------------------------------
# Configure environment variables
project_id = os.getenv("GCP_PROJECT_ID")
search_location = os.getenv("VERTEX_SEARCH_LOCATION", "global")
model_location = os.getenv("GCP_REGION", "us-central1")

# Initialize the model with Gemini 1.5 Flash (using GCP Vertex AI instead of Google AI Studio)
model = Gemini(
    model="gemini-1.5-flash", 
    client_kwargs={
        "vertexai": True,
        "project": project_id,
        "location": model_location
    }
)
data_store_id = os.getenv("VERTEX_DATA_STORE_ID")

# The tool expects the fully qualified resource name
full_data_store_path = f"projects/{project_id}/locations/{search_location}/collections/default_collection/dataStores/{data_store_id}"

retrieval_tool = VertexAiSearchTool(
    data_store_id=full_data_store_path
)

from google.adk.tools.mcp_tool import SseConnectionParams

# Set up the custom MCP server via SSE
hr_mcp_toolset = McpToolset(
    connection_params=SseConnectionParams(
        url="https://zcs-assessment-782492088107.us-central1.run.app/sse"
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
            
        except (KeyboardInterrupt, EOFError):
            print("\nHR Assistant: Goodbye!")
            break

if __name__ == "__main__":
    asyncio.run(main())
