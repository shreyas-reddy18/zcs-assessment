import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Initialize MCP server and other resources during startup
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize MCP server, BigQuery client, etc.
    print("Starting up HR MCP Server...")
    # TODO: Initialize db and MCP tools here
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

# TODO: Add MCP SDK integration (e.g., SSE transport endpoint for MCP)
