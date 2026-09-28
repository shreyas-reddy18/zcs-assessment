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

# Provide an SSE endpoint for MCP clients
app.mount("/mcp", mcp.asgi())
