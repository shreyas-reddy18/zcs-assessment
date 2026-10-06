"""Deploy the ADK agent to Vertex AI Agent Engine.

    python -m scripts.deploy_agent            # update AGENT_ENGINE_RESOURCE if set, else create
    python -m scripts.deploy_agent --create   # force a new engine

Updating in place keeps one engine and one stable resource name for the gateway.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)  # extra_packages are resolved relative to the working directory
load_dotenv(ROOT / ".env")

import vertexai  # noqa: E402
from vertexai.agent_engines import AdkApp  # noqa: E402

from agent.agent import root_agent  # noqa: E402

RUNTIME_ENV = ["GCP_PROJECT_ID", "GCP_REGION", "AGENT_MODEL", "VERTEX_SEARCH_LOCATION", "VERTEX_DATA_STORE_ID",
               "MCP_URL", "AS_OF_DATE"]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--create", action="store_true", help="Create a new engine even if AGENT_ENGINE_RESOURCE is set")
    args = p.parse_args()

    project, region = os.environ["GCP_PROJECT_ID"], os.getenv("GCP_REGION", "us-central1")
    client = vertexai.Client(project=project, location=region)
    config = {
        "display_name": "Meridian HR Assistant",
        "description": "ADK agent over Vertex AI Search (policies) and the Meridian MCP server (records, PTO writes).",
        "staging_bucket": os.environ["AGENT_STAGING_BUCKET"],
        "requirements": (ROOT / "agent" / "requirements.txt").read_text().split(),
        "extra_packages": ["agent"],
        "env_vars": {k: os.environ[k] for k in RUNTIME_ENV if os.getenv(k)},
    }
    if os.getenv("AGENT_SERVICE_ACCOUNT"):
        config["service_account"] = os.environ["AGENT_SERVICE_ACCOUNT"]

    app = AdkApp(agent=root_agent, enable_tracing=True)
    existing = os.getenv("AGENT_ENGINE_RESOURCE")
    if existing and not args.create:
        print(f"Updating {existing} ...")
        engine = client.agent_engines.update(name=existing, agent=app, config=config)
    else:
        print("Creating a new Agent Engine ...")
        engine = client.agent_engines.create(agent=app, config=config)
    print(f"Deployed: {engine.api_resource.name}")
    print("Set AGENT_ENGINE_RESOURCE to this value for the gateway.")


if __name__ == "__main__":
    main()
