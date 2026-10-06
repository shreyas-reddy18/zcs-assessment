"""Root ADK agent: one LLM agent composing the policy data store tool with the custom MCP server.

A single agent, rather than a policy sub-agent behind a transfer, so one turn can
combine records and policy (e.g. "can I take the week of July 13 off?") with no
hand-off loop.
"""
from __future__ import annotations

import time

import google.auth.transport.requests
from google.adk.agents import Agent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.models import Gemini
from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams
from google.genai import types
from google.oauth2 import id_token

from . import config
from .policy_search import list_policy_editions, search_policy_documents
from .prompt import INSTRUCTION

_token_cache: dict[str, tuple[float, str]] = {}


def _cloud_run_id_token(audience: str) -> str:
    """ID token for invoking the IAM-protected MCP service, cached for most of its 1-hour life."""
    cached = _token_cache.get(audience)
    if cached and cached[0] > time.time():
        return cached[1]
    token = id_token.fetch_id_token(google.auth.transport.requests.Request(), audience)
    _token_cache[audience] = (time.time() + 45 * 60, token)
    return token


def mcp_headers(ctx: ReadonlyContext) -> dict[str, str]:
    """Identity and provenance for every MCP call, taken from the session, not from the model.

    user_id is the verified Google email the gateway started the session with.
    """
    headers = {
        "X-Meridian-User": ctx.user_id,
        "X-Meridian-Session": ctx.session.id,
        "X-Meridian-Invocation": ctx.invocation_id,
    }
    if config.MCP_URL.startswith("https://"):
        audience = config.MCP_URL.rsplit("/mcp", 1)[0]
        headers["Authorization"] = f"Bearer {_cloud_run_id_token(audience)}"
    return headers


def instruction(ctx: ReadonlyContext) -> str:
    return INSTRUCTION.format(today=config.today().strftime("%A, %B %d, %Y"))


hr_records = McpToolset(
    connection_params=StreamableHTTPConnectionParams(url=config.MCP_URL, timeout=30.0),
    header_provider=mcp_headers,
)

root_agent = Agent(
    name="meridian_hr_assistant",
    # Vertex AI's shared Gemini quota returns transient 429s under load; back off and retry.
    model=Gemini(model=config.MODEL, retry_options=types.HttpRetryOptions(
        attempts=6, initial_delay=2, max_delay=30, http_status_codes=[429, 500, 503])),
    generate_content_config=types.GenerateContentConfig(temperature=0.1),
    description="Answers Meridian Dynamics policy and PTO questions for the signed-in employee and submits PTO requests.",
    instruction=instruction,
    tools=[search_policy_documents, list_policy_editions, hr_records],
)
