import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from dotenv import load_dotenv
from auth import get_current_user_email, get_user_identity
from tools import mcp

# Load environment variables from .env file
load_dotenv()

# Initialize MCP server and other resources during startup
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize MCP server, BigQuery client, etc.
    print("Starting up HR MCP Server...")
    yield
    # Shutdown: Clean up resources
    print("Shutting down HR MCP Server...")

# Initialize FastAPI app
app = FastAPI(
    title="HR Conversational Knowledge Agent MCP Server",
    description="Tooling layer for HR agent querying BigQuery with OAuth 2.0",
    version="0.1.0",
    lifespan=lifespan
)

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # For dev purposes
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health_check():
    """Basic health-check endpoint to verify the server is running."""
    return {
        "status": "healthy",
        "project_id": os.getenv("GCP_PROJECT_ID", "Not configured")
    }

@app.get("/auth/verify")
async def verify_auth(email: str = Depends(get_current_user_email)):
    """
    Endpoint that accepts a Google OAuth 2.0 ID token (via Authorization header as Bearer token),
    verifies it, and returns the user's mapped identity info from BigQuery.
    """
    identity = get_user_identity(email)
    return {
        "email": email,
        "identity": identity
    }
    
from pydantic import BaseModel
from agent import hr_assistant, runner

class ChatRequest(BaseModel):
    message: str

@app.post("/api/chat")
async def chat_endpoint(request: ChatRequest, email: str = Depends(get_current_user_email)):
    # 1. HARD BLOCK UNMAPPED USERS IMMEDIATELY
    # This imports your existing auth function. If the email isn't in BigQuery, 
    # it automatically throws a 403 error and stops execution.
    from auth import get_user_identity
    from fastapi import HTTPException

    try:
        identity = get_user_identity(email)
    except HTTPException as e:
        if e.status_code == 403:
            return {"response": "This email is not associated with an employee."}
        raise e
    
    # THE MISSING PIECE: Manually trigger the rejection if the result is empty/None
    if not identity or (isinstance(identity, dict) and "employee_id" not in identity):
        return {"response": "This email is not associated with an employee."}
    
    from agent import SYSTEM_INSTRUCTION
    
    # Create a dynamic instruction by combining the base persona with the authenticated user
    dynamic_instruction = f"{SYSTEM_INSTRUCTION}\n\n[Security Context]\nThe current authenticated user is: {email}"
    
    # We update the agent's system instruction per user session
    hr_assistant.instruction = dynamic_instruction
    
    # Use your stable session prefix
    session_id = f"demo_v2_{email}"
    
    try:
        from agent import session_service
        # Check if the session exists; if not, create it
        session = await session_service.get_session(
            app_name="meridian_hr_app",
            user_id=email,
            session_id=session_id
        )
        if session is None:
            await session_service.create_session(
                app_name="meridian_hr_app",
                user_id=email,
                session_id=session_id
            )
            
        from google.adk.utils.content_utils import to_user_content, extract_text_from_content
        # Run the agent
        events = runner.run_async(
            user_id=email,
            session_id=session_id,
            new_message=to_user_content(request.message)
        )
        
        final_text = ""
        async for event in events:
            if getattr(event, 'is_final_response', False) and getattr(event, 'message', None):
                # Extract text using ADK's utility
                text = extract_text_from_content(event.message)
                if text.strip():
                    final_text = text
                    
        return {"response": final_text, "email": email}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"response": f"Error processing your request: {str(e)}"}
