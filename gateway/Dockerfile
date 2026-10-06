# Gateway image: React frontend + FastAPI gateway. Build context: repository root.
FROM node:20-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 FRONTEND_DIST=/app/frontend/dist
WORKDIR /app
COPY gateway/requirements.txt gateway/requirements.txt
RUN pip install --no-cache-dir -r gateway/requirements.txt
COPY gateway gateway
COPY --from=frontend /frontend/dist frontend/dist
CMD exec uvicorn gateway.app:app --host 0.0.0.0 --port ${PORT:-8080}
