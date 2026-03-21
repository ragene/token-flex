#!/bin/bash
# Build and push token-flow Docker image to ECR
set -e

REGION=us-west-2
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
ECR="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"
IMAGE="$ECR/token-flow:latest"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "🔧 token-flow Docker build + push"
echo "Image: $IMAGE"
echo "======================================"

# Login to ECR
aws ecr get-login-password --region $REGION | \
  docker login --username AWS --password-stdin "$ECR"

# Ensure ECR repo exists
aws ecr describe-repositories --repository-names token-flow --region $REGION \
  --query 'repositories[0].repositoryUri' --output text 2>/dev/null || \
  aws ecr create-repository --repository-name token-flow --region $REGION \
    --query 'repository.repositoryUri' --output text

# Build — context is the token-flow directory
docker build \
  -f "$SCRIPT_DIR/Dockerfile" \
  -t token-flow:latest \
  -t "$IMAGE" \
  "$SCRIPT_DIR"

echo "📤 Pushing..."
docker push "$IMAGE"

echo ""
echo "✅ Done. Forcing ECS service redeployment..."
aws ecs update-service \
  --cluster freightdawg-cluster \
  --service token-flow-service \
  --force-new-deployment \
  --region $REGION \
  --query 'service.deployments[0].{status:status,desired:desiredCount}' \
  --output table

echo "🚀 Deployment triggered."
