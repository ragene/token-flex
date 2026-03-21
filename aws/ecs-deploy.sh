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

# ── 3. Force new deployment on token-flow-ui service ─────────────────────────
echo ""
echo "🚀 Forcing new deployment on $CLUSTER/$SERVICE..."
aws ecs update-service \
  --cluster $CLUSTER \
  --service $SERVICE \
  --force-new-deployment \
  --region $REGION \
  --query 'service.{Status:status,Desired:desiredCount}' \
  --output table

# ── 4. Wait for stable ───────────────────────────────────────────────────────
echo ""
echo "⏳ Waiting for service to stabilize (this may take 2-4 min)..."
aws ecs wait services-stable --cluster $CLUSTER --services $SERVICE --region $REGION
echo "  ✓ Service stable"

echo ""
echo "✅ Deployed → https://token-flow.thefreightdawg.com"
echo "   Health:  https://token-flow.thefreightdawg.com/health"
echo "   Docs:    https://token-flow.thefreightdawg.com/docs"
