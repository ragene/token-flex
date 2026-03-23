#!/bin/bash
# Deploy the token-flow API to the token-flow-ui ECS cluster.
#
# This builds/pushes the token-flow:latest Docker image, then triggers a
# force-new-deployment on the token-flow-ui service so the running task
# picks up the new image. The token-flow-ui task definition (managed by
# token-flow-ui/deploy.sh) includes Envoy + UI + this API container.
#
# Usage:
#   ./ecs-deploy.sh             # build, push, deploy
#   ./ecs-deploy.sh --skip-build  # skip build, just force-redeploy
set -e

REGION=us-west-2
CLUSTER=token-flow-ui
SERVICE=token-flow-ui
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
ECR="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"
IMAGE="$ECR/token-flow:latest"

echo "🔧 token-flow ECS Deploy"
echo "Account: $ACCOUNT | Region: $REGION | Cluster: $CLUSTER"
echo "=================================="

# ── 1. Ensure ECR repo exists ────────────────────────────────────────────────
echo "📦 Ensuring ECR repo 'token-flow'..."
aws ecr describe-repositories --repository-names token-flow --region $REGION \
  --query 'repositories[0].repositoryUri' --output text 2>/dev/null || \
  aws ecr create-repository --repository-name token-flow --region $REGION \
    --query 'repository.repositoryUri' --output text
echo "  ✓ ECR repo ready"

# ── 2. Build & push Docker image ─────────────────────────────────────────────
if [[ "$1" != "--skip-build" ]]; then
  echo ""
  echo "🐳 Building and pushing Docker image..."
  aws ecr get-login-password --region $REGION | \
    docker login --username AWS --password-stdin "$ECR"

  cd "$(dirname "$0")/.."
  docker build -t "$IMAGE" . 2>&1 | tail -5
  docker push "$IMAGE" 2>&1 | tail -3
  echo "  ✓ Image pushed: $IMAGE"
else
  echo "  ⏭  Skipping build (--skip-build)"
fi

# ── 3. Patch task definition with required env vars ───────────────────────────
# Read secrets from .env (same dir as this script's parent) so they are
# never hardcoded in source but always applied on every deploy.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/../.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "❌ .env not found at $ENV_FILE — cannot inject secrets into task definition"
  exit 1
fi

# Source only the vars we need (avoid polluting the shell with everything)
_AUTH0_DOMAIN=$(grep   '^AUTH0_DOMAIN='          "$ENV_FILE" | cut -d= -f2-)
_AUTH0_CLIENT=$(grep   '^AUTH0_CLIENT_ID='       "$ENV_FILE" | cut -d= -f2-)
_SECRET_KEY=$(grep     '^SECRET_KEY='            "$ENV_FILE" | cut -d= -f2-)
_SHARED_TOKEN=$(grep   '^TOKEN_FLOW_AUTH_TOKEN=' "$ENV_FILE" | cut -d= -f2-)

if [[ -z "$_AUTH0_DOMAIN" || -z "$_SECRET_KEY" ]]; then
  echo "❌ AUTH0_DOMAIN or SECRET_KEY missing from .env"
  exit 1
fi

echo ""
echo "🔐 Patching task definition with AUTH0 + SECRET_KEY env vars..."

# Fetch current task def, patch the token-flow container env, register new revision
_TD_FILE=$(mktemp /tmp/tf-taskdef-XXXXXX.json)
_TD_PATCHED=$(mktemp /tmp/tf-taskdef-patched-XXXXXX.json)
trap "rm -f $_TD_FILE $_TD_PATCHED" EXIT

aws ecs describe-task-definition \
  --task-definition "$SERVICE" --region "$REGION" \
  --query 'taskDefinition' --output json > "$_TD_FILE"

python3 << PYEOF
import json

with open("$_TD_FILE") as f:
    td = json.load(f)

patches = {
    "AUTH0_DOMAIN":          "$_AUTH0_DOMAIN",
    "AUTH0_CLIENT_ID":       "$_AUTH0_CLIENT",
    "SECRET_KEY":            "$_SECRET_KEY",
    "TOKEN_FLOW_AUTH_TOKEN": "$_SHARED_TOKEN",
}

for c in td["containerDefinitions"]:
    if c["name"] == "token-flow":
        env = {e["name"]: e["value"] for e in c.get("environment", [])}
        env.update(patches)
        c["environment"] = [{"name": k, "value": v} for k, v in env.items()]

# Strip read-only fields before re-registering
for key in ["taskDefinitionArn","revision","status","requiresAttributes",
            "placementConstraints","compatibilities","registeredAt","registeredBy"]:
    td.pop(key, None)

with open("$_TD_PATCHED", "w") as f:
    json.dump(td, f)
PYEOF

NEW_ARN=$(aws ecs register-task-definition \
  --region "$REGION" \
  --cli-input-json "file://$_TD_PATCHED" \
  --query 'taskDefinition.taskDefinitionArn' --output text)
echo "  ✓ New task def: $NEW_ARN"

# ── 4. Deploy new task definition ─────────────────────────────────────────────
echo ""
echo "🚀 Deploying $NEW_ARN to $CLUSTER/$SERVICE..."
aws ecs update-service \
  --cluster $CLUSTER \
  --service $SERVICE \
  --task-definition "$NEW_ARN" \
  --force-new-deployment \
  --region $REGION \
  --query 'service.{Status:status,Desired:desiredCount}' \
  --output table

# ── 5. Wait for stable ───────────────────────────────────────────────────────
echo ""
echo "⏳ Waiting for service to stabilize (this may take 2-4 min)..."
aws ecs wait services-stable --cluster $CLUSTER --services $SERVICE --region $REGION
echo "  ✓ Service stable"

echo ""
echo "✅ Deployed → https://token-flow.thefreightdawg.com"
echo "   Health:  https://token-flow.thefreightdawg.com/health"
echo "   Docs:    https://token-flow.thefreightdawg.com/docs"
