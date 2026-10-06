"""Agent configuration, read from the environment (see .env.example)."""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "")
REGION = os.getenv("GCP_REGION", "us-central1")
MODEL = os.getenv("AGENT_MODEL", "gemini-2.5-flash")

SEARCH_LOCATION = os.getenv("VERTEX_SEARCH_LOCATION", "global")
DATA_STORE_ID = os.environ.get("VERTEX_DATA_STORE_ID", "")

# Streamable-HTTP endpoint of the MCP server, e.g. https://meridian-mcp-xyz.a.run.app/mcp
MCP_URL = os.getenv("MCP_URL", "http://127.0.0.1:8081/mcp")

# Optional fixed "today"; must match the MCP server's AS_OF_DATE. Unset = real date.
AS_OF_DATE = os.getenv("AS_OF_DATE", "").strip()

# Gemini via Vertex AI. Agent Engine reserves the GOOGLE_CLOUD_* names, so we
# derive them from our own variables only when the runtime has not set them.
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
if PROJECT_ID:
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", PROJECT_ID)
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", REGION)


def today() -> date:
    if AS_OF_DATE:
        return date.fromisoformat(AS_OF_DATE)
    return datetime.now(timezone(timedelta(hours=-6))).date()  # company HQ: Chicago


def data_store_path() -> str:
    return (f"projects/{PROJECT_ID}/locations/{SEARCH_LOCATION}"
            f"/collections/default_collection/dataStores/{DATA_STORE_ID}")
