# MCP server image. Build context: repository root.
#   gcloud builds submit --config deploy/cloudbuild.mcp.yaml
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY mcp_server/requirements.txt mcp_server/requirements.txt
RUN pip install --no-cache-dir -r mcp_server/requirements.txt

COPY mcp_server mcp_server

CMD exec uvicorn mcp_server.server:app --host 0.0.0.0 --port ${PORT:-8080}
