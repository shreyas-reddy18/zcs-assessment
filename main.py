import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# The prompt mentions tools.py, but we created mcp_server.py per the previous instruction
# to contain the pure MCP data layer. We will import the mcp instance from there.
from tools import mcp

app = FastAPI(
    title="HR MCP Server (SSE)",
    description="Tooling layer for HR agent over SSE"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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

# Bind the MCP instance to the FastAPI app so it exposes the standard /sse and /messages endpoints
app.mount("/sse", mcp.sse_app)
