#!/bin/bash
# Deploy token-flow-ui to ECS: one cluster, one task (envoy + API + UI), one ALB
# Usage: ./deploy.sh [--skip-build]
#
# Mirrors FreightDawg ecs-deploy.sh model.
# Cluster/service: token-flow-ui
# ALB: token-flow-ui-alb → token-flow-ui-tg (port 8080) → envoy container
# URL: https://token-flow.thefreightdawg.com
set -e

REGION=us-west-2
CLUSTER=token-flow-ui
SERVICE=token-flow-ui
FAMILY=token-flow-ui
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
ECR="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"

# The CORRECT TG — attached to token-flow-ui-alb 443 listener
TG_ARN="arn:aws:elasticloadbalancing:us-west-2:531948420901:targetgroup/token-flow-ui-tg/c0852d119fa8e0cb"
SUBNET1="subnet-086393d55fe5dfeb3"
SUBNET2="subnet-09d5d88fb7009a118"
SG="sg-077aaa9d4de6075a2"

echo "🔧 token-flow unified ECS Deploy"
echo "Account: $ACCOUNT | Region: $REGION | Cluster: $CLUSTER"
echo "=================================="

# ── 1. Ensure ECS cluster ────────────────────────────────────────────────────
echo "🏗  Ensuring ECS cluster '$CLUSTER' exists..."
CLUSTER_STATUS=$(aws ecs describe-clusters --clusters $CLUSTER --region $REGION \
  --query 'clusters[0].status' --output text 2>/dev/null || echo "MISSING")
if [[ "$CLUSTER_STATUS" != "ACTIVE" ]]; then
  aws ecs create-cluster --cluster-name $CLUSTER --region $REGION \
    --capacity-providers FARGATE FARGATE_SPOT \
    --query 'cluster.{Name:clusterName,Status:status}' --output table
  echo "  ✓ Cluster created"
else
  echo "  ✓ Cluster already active"
fi

# ── 2. Ensure ECR repos ───────────────────────────────────────────────────────
echo ""
for REPO in token-flow token-flow-ui token-flow-ui-envoy; do
  echo "📦 Ensuring ECR repo '$REPO'..."
  aws ecr describe-repositories --repository-names $REPO --region $REGION \
    --query 'repositories[0].repositoryUri' --output text 2>/dev/null || \
    aws ecr create-repository --repository-name $REPO --region $REGION \
      --query 'repository.repositoryUri' --output text
done
echo "  ✓ ECR repos ready"

# ── 3. Ensure CloudWatch log group ────────────────────────────────────────────
echo ""
echo "📝 Ensuring log group '/ecs/token-flow-ui' exists..."
aws logs create-log-group --log-group-name /ecs/token-flow-ui --region $REGION 2>/dev/null || true
echo "  ✓ Log group ready"

# ── 4. Build & push all 3 images ─────────────────────────────────────────────
if [[ "$1" != "--skip-build" ]]; then
  echo ""
  echo "🐳 Building and pushing images..."
  aws ecr get-login-password --region $REGION | \
    docker login --username AWS --password-stdin "$ECR"

  SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
  WORKSPACE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

  echo "  → token-flow API (from $WORKSPACE_DIR/token-flow)"
  docker build -f "$WORKSPACE_DIR/token-flow/Dockerfile" \
    -t "$ECR/token-flow:latest" \
    "$WORKSPACE_DIR/token-flow" 2>&1 | tail -5
  docker push "$ECR/token-flow:latest" 2>&1 | tail -3
  echo "  ✓ token-flow API image pushed"

  echo "  → token-flow-ui (from $SCRIPT_DIR)"
  docker build -t "$ECR/token-flow-ui:latest" "$SCRIPT_DIR" 2>&1 | tail -5
  docker push "$ECR/token-flow-ui:latest" 2>&1 | tail -3
  echo "  ✓ token-flow-ui image pushed"

  echo "  → token-flow-ui-envoy (from $SCRIPT_DIR/envoy)"
  docker build -f "$SCRIPT_DIR/envoy/Dockerfile.envoy" \
    -t "$ECR/token-flow-ui-envoy:latest" \
    "$SCRIPT_DIR/envoy" 2>&1 | tail -5
  docker push "$ECR/token-flow-ui-envoy:latest" 2>&1 | tail -3
  echo "  ✓ Envoy image pushed"
else
  echo "  ⏭  Skipping build (--skip-build)"
fi

# ── 5. Register task definition ───────────────────────────────────────────────
echo ""
echo "📋 Registering task definition '$FAMILY'..."
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cat "$SCRIPT_DIR/task-definition.json" \
  | sed "s|531948420901|$ACCOUNT|g" > /tmp/td-token-flow-ui.json
TASK_DEF=$(aws ecs register-task-definition --region $REGION \
  --cli-input-json file:///tmp/td-token-flow-ui.json \
  --query 'taskDefinition.{Revision:revision,Family:family}' 2>&1)

echo "  Registered: $TASK_DEF"
REVISION=$(echo "$TASK_DEF" | python3 -c "import sys,json; print(json.load(sys.stdin)['Revision'])")

# ── 6. Create or update ECS service ──────────────────────────────────────────
echo ""
echo "🚀 Deploying ECS service '$SERVICE' on cluster '$CLUSTER'..."
SERVICE_INFO=$(aws ecs describe-services --cluster $CLUSTER --services $SERVICE \
  --region $REGION --query 'services[0].{status:status,lb:loadBalancers[0].targetGroupArn}' \
  --output json 2>/dev/null || echo '{"status":"NONE","lb":null}')

SERVICE_STATUS=$(echo "$SERVICE_INFO" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status','NONE'))")
CURRENT_TG=$(echo "$SERVICE_INFO" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('lb') or '')")

echo "  Current service status: $SERVICE_STATUS"
echo "  Current TG: ${CURRENT_TG:-none}"

# If the service exists but is on the WRONG target group, drain and delete it first
if [[ "$SERVICE_STATUS" == "ACTIVE" && "$CURRENT_TG" != "$TG_ARN" ]]; then
  echo "  ⚠️  Service is on wrong TG. Draining and recreating..."
  aws ecs update-service --cluster $CLUSTER --service $SERVICE \
    --desired-count 0 --region $REGION > /dev/null
  echo "  Waiting for tasks to drain..."
  sleep 30
  aws ecs delete-service --cluster $CLUSTER --service $SERVICE \
    --force --region $REGION > /dev/null
  echo "  Waiting for service deletion..."
  sleep 15
  SERVICE_STATUS="NONE"
fi

if [[ "$SERVICE_STATUS" == "ACTIVE" ]]; then
  aws ecs update-service \
    --cluster $CLUSTER --service $SERVICE \
    --task-definition $FAMILY:$REVISION \
    --force-new-deployment \
    --region $REGION \
    --query 'service.{Status:status,Desired:desiredCount}' --output table
  echo "  ✓ Service updated"
else
  echo "  Creating service with ALB target group..."
  aws ecs create-service \
    --cluster $CLUSTER \
    --service-name $SERVICE \
    --task-definition $FAMILY:$REVISION \
    --launch-type FARGATE \
    --desired-count 1 \
    --network-configuration "awsvpcConfiguration={subnets=[$SUBNET1,$SUBNET2],securityGroups=[$SG],assignPublicIp=ENABLED}" \
    --load-balancers "[{\"targetGroupArn\":\"$TG_ARN\",\"containerName\":\"envoy\",\"containerPort\":8080}]" \
    --deployment-configuration "minimumHealthyPercent=100,maximumPercent=200" \
    --region $REGION \
    --query 'service.{Status:status,Desired:desiredCount}' --output table
  echo "  ✓ Service created"
fi

# ── 7. Wait for stable ────────────────────────────────────────────────────────
echo ""
echo "⏳ Waiting for service to stabilize (this may take 3-6 min)..."
aws ecs wait services-stable --cluster $CLUSTER --services $SERVICE --region $REGION && \
  echo "  ✓ Service stable" || \
  echo "  ⚠️  Timed out — check: aws ecs describe-services --cluster $CLUSTER --services $SERVICE --region $REGION"

# ── 8. Done ───────────────────────────────────────────────────────────────────
echo ""
echo "✅ Deployed! → https://token-flow.thefreightdawg.com"
