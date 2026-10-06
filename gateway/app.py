"""Web gateway: Google Sign-In verification, chat API, and the static frontend.

This is where identity enters the system. The browser sends the Google ID token;
the gateway verifies its signature and audience, and uses the verified email as
the agent session's user_id. Everything downstream (agent -> MCP headers ->
BigQuery visible_employees()) keys off that value. Nothing the user types can
change it.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import google.auth.transport.requests
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from google.oauth2 import id_token
from pydantic import BaseModel, Field

from .agent_client import AgentBackend, AgentEngineBackend, LocalBackend, summarize

load_dotenv()
log = logging.getLogger("meridian.gateway")

OAUTH_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
BACKEND = os.getenv("AGENT_BACKEND", "agent_engine")  # agent_engine | local
FRONTEND_DIST = Path(os.getenv("FRONTEND_DIST", Path(__file__).resolve().parent.parent / "frontend" / "dist"))

app = FastAPI(title="Meridian HR Assistant")
_bearer = HTTPBearer(auto_error=False)
_google_request = google.auth.transport.requests.Request()
_backend: AgentBackend | None = None


def backend() -> AgentBackend:
    global _backend
    if _backend is None:
        if BACKEND == "local":
            _backend = LocalBackend()
        else:
            _backend = AgentEngineBackend(os.environ["AGENT_ENGINE_RESOURCE"], os.getenv("GCP_REGION", "us-central1"))
    return _backend


def signed_in_email(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
    """Verify the Google ID token. Any Google account may sign in; authorization happens downstream."""
    if creds is None:
        raise HTTPException(401, "Sign in with Google first.")
    try:
        claims = id_token.verify_oauth2_token(creds.credentials, _google_request, OAUTH_CLIENT_ID)
    except ValueError as e:
        raise HTTPException(401, f"Invalid or expired Google sign-in: {e}")
    if not claims.get("email") or not claims.get("email_verified"):
        raise HTTPException(401, "Google account has no verified email address.")
    return claims["email"].lower()


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)


@app.get("/health")
def healthz() -> dict:
    return {"status": "ok", "backend": BACKEND}


@app.get("/api/config")
def public_config() -> dict:
    return {"google_client_id": OAUTH_CLIENT_ID}


@app.post("/api/sessions")
async def create_session(email: str = Depends(signed_in_email)) -> dict:
    session_id = await backend().create_session(email)
    log.info("session %s created for %s", session_id, email)
    return {"session_id": session_id, "email": email}


@app.post("/api/chat")
async def chat(req: ChatRequest, email: str = Depends(signed_in_email)) -> dict:
    try:
        events = await backend().run(email, req.session_id, req.message)
        result = summarize(events)
    except Exception as e:  # keep the UI usable; details go to the logs
        log.exception("agent call failed for %s", email)
        raise HTTPException(502, f"The assistant could not complete that request ({type(e).__name__}).")
    return {"reply": result["reply"] or "(no response)", "tools_used": result["tools_used"]}


if FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        return FileResponse(FRONTEND_DIST / "index.html")
