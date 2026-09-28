"""
Meridian Dynamics HR Conversational Assistant.
Initializes the core LlmAgent using Google's Agent Development Kit (ADK) and Gemini 1.5 Pro.
"""

from google_adk import LlmAgent, Runner, InMemorySessionService
from google_adk.models import GeminiModel

# ---------------------------------------------------------------------------
# Persona & System Instructions
# ---------------------------------------------------------------------------
SYSTEM_INSTRUCTION = """
You are the official internal HR Conversational Assistant for Meridian Dynamics.
Your primary role is to assist employees and managers with HR-related inquiries, 
PTO requests, and company policy information.

CORE RULES & RESPONSIBILITIES:
1. Retrieval First: Always rely on your designated retrieval tool for answering questions 
   about company policies, benefits, and guidelines. Do not invent or hallucinate policies.
2. Strict Authorization: When interacting with employee data (like PTO balances or requests), 
   enforce strict authorization boundaries by properly utilizing your backend tools. Never 
   disclose data belonging to another employee unless authorized (e.g., manager accessing 
   a direct report's data or People Ops).
3. Explicit Confirmation: Before executing ANY write operations (such as submitting a PTO 
   request), you MUST present a clear summary of the action to the user and prompt them 
   for explicit confirmation. Only proceed with the database write once they have confirmed.
"""

# ---------------------------------------------------------------------------
# Agent Initialization
# ---------------------------------------------------------------------------
# Initialize the model with Gemini 1.5 Pro
model = GeminiModel(model_name="gemini-1.5-pro")

# Initialize the core LLM Agent
# Note: Tools (like the MCP client tools for BigQuery and retrieval) should be passed 
# in the tools list when fully integrated.
hr_assistant = LlmAgent(
    name="MeridianDynamics-HR-Assistant",
    model=model,
    system_instruction=SYSTEM_INSTRUCTION,
    tools=[] # TODO: Inject MCP client tools here
)

# ---------------------------------------------------------------------------
# Session & Execution Management
# ---------------------------------------------------------------------------
# Instantiate the session service to manage stateful conversations in memory
session_service = InMemorySessionService()

# Instantiate the Runner to manage the execution loop, invoking the agent 
# and maintaining conversation history within the session.
runner = Runner(
    agent=hr_assistant,
    session_service=session_service
)

def main():
    """
    Example execution loop for local testing.
    """
    # Create a new session for this interaction
    session = session_service.create_session()
    print(f"Started HR Assistant Session: {session.session_id}")
    print("HR Assistant: Hello! I am the Meridian Dynamics HR Assistant. How can I help you today?\n")
    
    while True:
        try:
            user_input = input("You: ")
            if user_input.lower() in ['exit', 'quit']:
                print("HR Assistant: Goodbye!")
                break
                
            # Run the agent over the user input
            response = runner.run(session_id=session.session_id, input_text=user_input)
            print(f"HR Assistant: {response.output_text}\n")
            
        except KeyboardInterrupt:
            print("\nHR Assistant: Goodbye!")
            break

if __name__ == "__main__":
    main()
