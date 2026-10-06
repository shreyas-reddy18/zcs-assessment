#!/usr/bin/env bash
# Build and deploy the two Cloud Run services and wire up IAM.
#   ./deploy/deploy_services.sh mcp       # MCP server (private)
#   ./deploy/deploy_services.sh gateway   # web gateway + frontend (public)
# Reads configuration from .env. Run from the repository root.
set -euo pipefail
set -a; source .env; set +a

: "${GCP_PROJECT_ID:?}" "${GCP_REGION:?}" "${ARTIFACT_REPO:=cloud-run-source-deploy}"
REGISTRY="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/${ARTIFACT_REPO}"
TAG="$(git rev-parse --short HEAD 2>/dev/null || date +%s)"

gcloud artifacts repositories describe "$ARTIFACT_REPO" --location "$GCP_REGION" --project "$GCP_PROJECT_ID" >/dev/null 2>&1 \
  || gcloud artifacts repositories create "$ARTIFACT_REPO" --repository-format docker --location "$GCP_REGION" --project "$GCP_PROJECT_ID"

build() {  # build <dockerfile> <image>
  # Build as the deploy service account (BUILD_SERVICE_ACCOUNT, default MCP_SERVICE_ACCOUNT),
  # not the Compute default account.
  gcloud builds submit --project "$GCP_PROJECT_ID" --config deploy/cloudbuild.yaml \
    --service-account "projects/$GCP_PROJECT_ID/serviceAccounts/${BUILD_SERVICE_ACCOUNT:-$MCP_SERVICE_ACCOUNT}" \
    --substitutions "_DOCKERFILE=$1,_IMAGE=$2" .
}

case "${1:-}" in
  mcp)
    : "${MCP_SERVICE_ACCOUNT:?}" "${AGENT_RUNTIME_IDENTITY:?}"
    IMAGE="$REGISTRY/meridian-mcp:$TAG"
    build mcp_server/Dockerfile "$IMAGE"
    # Private: Cloud Run rejects any caller without run.invoker. One instance keeps
    # the per-request write lock meaningful (see README, "Write path").
    gcloud run deploy meridian-mcp --project "$GCP_PROJECT_ID" --region "$GCP_REGION" --image "$IMAGE" \
      --no-allow-unauthenticated --service-account "$MCP_SERVICE_ACCOUNT" --max-instances 1 \
      --set-env-vars "GCP_PROJECT_ID=$GCP_PROJECT_ID,BQ_DATASET=$BQ_DATASET,HOST=0.0.0.0,AS_OF_DATE=${AS_OF_DATE:-}"
    # Only the agent's runtime identity may call it.
    gcloud run services add-iam-policy-binding meridian-mcp --project "$GCP_PROJECT_ID" --region "$GCP_REGION" \
      --member "serviceAccount:$AGENT_RUNTIME_IDENTITY" --role roles/run.invoker
    echo "MCP_URL=$(gcloud run services describe meridian-mcp --project "$GCP_PROJECT_ID" --region "$GCP_REGION" --format 'value(status.url)')/mcp"
    ;;
  gateway)
    : "${GATEWAY_SERVICE_ACCOUNT:?}" "${AGENT_ENGINE_RESOURCE:?}" "${GOOGLE_OAUTH_CLIENT_ID:?}"
    IMAGE="$REGISTRY/meridian-gateway:$TAG"
    build gateway/Dockerfile "$IMAGE"
    gcloud run deploy meridian-gateway --project "$GCP_PROJECT_ID" --region "$GCP_REGION" --image "$IMAGE" \
      --allow-unauthenticated --service-account "$GATEWAY_SERVICE_ACCOUNT" \
      --set-env-vars "GCP_REGION=$GCP_REGION,AGENT_ENGINE_RESOURCE=$AGENT_ENGINE_RESOURCE,GOOGLE_OAUTH_CLIENT_ID=$GOOGLE_OAUTH_CLIENT_ID"
    URL="$(gcloud run services describe meridian-gateway --project "$GCP_PROJECT_ID" --region "$GCP_REGION" --format 'value(status.url)')"
    echo "Gateway: $URL  (add it to the OAuth client's Authorized JavaScript origins)"
    ;;
  *)
    echo "usage: $0 mcp|gateway" >&2; exit 1 ;;
esac
