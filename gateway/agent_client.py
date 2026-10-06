"""Clients for the deployed agent (Vertex AI Agent Engine) and an in-process fallback for local dev."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Protocol

import google.auth
import google.auth.transport.requests
import httpx


class AgentBackend(Protocol):
    async def create_session(self, user_id: str) -> str: ...
    async def run(self, user_id: str, session_id: str, message: str) -> list[dict]: ...


class AgentEngineBackend:
    """Calls the AdkApp deployed on Agent Engine through its REST API."""

    def __init__(self, resource_name: str, region: str):
        self._base = f"https://{region}-aiplatform.googleapis.com/v1/{resource_name}"
        self._creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0))

    async def _headers(self) -> dict[str, str]:
        if not self._creds.valid:
            await asyncio.to_thread(self._creds.refresh, google.auth.transport.requests.Request())
        return {"Authorization": f"Bearer {self._creds.token}", "Content-Type": "application/json"}

    async def create_session(self, user_id: str) -> str:
        resp = await self._http.post(
            f"{self._base}:query",
            headers=await self._headers(),
            json={"class_method": "async_create_session", "input": {"user_id": user_id}},
        )
        resp.raise_for_status()
        return resp.json()["output"]["id"]

    async def run(self, user_id: str, session_id: str, message: str) -> list[dict]:
        body = {
            "class_method": "async_stream_query",
            "input": {"user_id": user_id, "session_id": session_id, "message": message},
        }
        events: list[dict] = []
        async with self._http.stream("POST", f"{self._base}:streamQuery", headers=await self._headers(),
                                     json=body) as resp:
            if resp.status_code != 200:
                raise RuntimeError(f"Agent Engine returned {resp.status_code}: {(await resp.aread())[:500]!r}")
            async for line in resp.aiter_lines():
                line = line.strip()
                if line.startswith("data:"):
                    line = line[5:].strip()
                if line:
                    events.append(json.loads(line))
        return events


class LocalBackend:
    """Runs the ADK agent in-process (`AGENT_BACKEND=local`). For development and as a demo fallback."""

    def __init__(self):
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService

        from agent.agent import root_agent

        self._sessions = InMemorySessionService()
        self._runner = Runner(app_name="meridian_hr", agent=root_agent, session_service=self._sessions)

    async def create_session(self, user_id: str) -> str:
        session = await self._sessions.create_session(app_name="meridian_hr", user_id=user_id)
        return session.id

    async def run(self, user_id: str, session_id: str, message: str) -> list[dict]:
        from google.genai import types

        content = types.Content(role="user", parts=[types.Part(text=message)])
        events = []
        async for event in self._runner.run_async(user_id=user_id, session_id=session_id, new_message=content):
            events.append(event.model_dump(mode="json", exclude_none=True))
        return events


def summarize(events: list[dict]) -> dict[str, Any]:
    """Final reply text (what the model said after its last tool interaction) and the tools it used."""
    reply: list[str] = []
    tools: list[str] = []
    for ev in events:
        if ev.get("error_message") or ev.get("errorMessage"):
            raise RuntimeError(ev.get("error_message") or ev.get("errorMessage"))
        for part in (ev.get("content") or {}).get("parts") or []:
            call = part.get("function_call") or part.get("functionCall")
            if call:
                tools.append(call.get("name"))
                reply = []
            elif part.get("function_response") or part.get("functionResponse"):
                reply = []
            elif part.get("text") and not part.get("thought"):
                reply.append(part["text"])
    return {"reply": "\n\n".join(t.strip() for t in reply if t.strip()), "tools_used": tools}
