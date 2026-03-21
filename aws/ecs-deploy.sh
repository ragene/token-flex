#!/bin/bash
# Deploy token-flow to its own ECS cluster (mirrors smart-memory deployment model)
# Usage: ./ecs-deploy.sh [--skip-build]
#
# token-flow runs on its own dedicated cluster/service, both named "token-flow".
# SSM: /token-flow/anthropic_api_key (copied from /smart-memory/anthropic_api_key if absent)
set -e

REGION=us-west-2
CLUSTER=token-flow
SERVICE=token-flow
FAMILY=token-flow
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
ECR="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"
IMAGE="$ECR/token-flow:latest"

echo "🔧 token-flow ECS Deploy"
echo "Account: $ACCOUNT | Region: $REGION | Cluster: $CLUSTER"
echo "=================================="

# ── 1. Ensure ECS cluster exists ─────────────────────────────────────────────
echo "🏗  Ensuring ECS cluster '$CLUSTER' exists..."
CLUSTER_STATUS=$(aws ecs describe-clusters --clusters $CLUSTER --region $REGION \
  --query 'clusters[0].status' --output text 2>/dev/null || echo "MISSING")

if [[ "$CLUSTER_STATUS" != "ACTIVE" ]]; then
  echo "  Creating cluster '$CLUSTER'..."
  aws ecs create-cluster --cluster-name $CLUSTER --region $REGION \
    --capacity-providers FARGATE FARGATE_SPOT \
    --query 'cluster.{Name:clusterName,Status:status}' --output table
  echo "  ✓ Cluster created"
else
  echo "  ✓ Cluster already active"
fi

# ── 2. Ensure ECR repo exists ─────────────────────────────────────────────────
echo ""
echo "📦 Ensuring ECR repo 'token-flow' exists..."
aws ecr describe-repositories --repository-names token-flow --region $REGION \
  --query 'repositories[0].repositoryUri' --output text 2>/dev/null || \
  aws ecr create-repository --repository-name token-flow --region $REGION \
    --query 'repository.repositoryUri' --output text
echo "  ✓ ECR repo ready"

# ── 3. Ensure CloudWatch log group exists ─────────────────────────────────────
echo ""
echo "📝 Ensuring log group '/ecs/token-flow' exists..."
aws logs create-log-group --log-group-name /ecs/token-flow --region $REGION 2>/dev/null || true
echo "  ✓ Log group ready"

# ── 4. Build & push Docker image ─────────────────────────────────────────────
if [[ "$1" != "--skip-build" ]]; then
  echo ""
  echo "🐳 Building and pushing Docker image..."
  aws ecr get-login-password --region $REGION | \
    docker login --username AWS --password-stdin "$ECR"

  # Build from token-flow/ root (Dockerfile is there)
  cd "$(dirname "$0")/.."

  docker build -t "$IMAGE" . 2>&1 | tail -5
  docker push "$IMAGE" 2>&1 | tail -3
  echo "  ✓ Image pushed: $IMAGE"
else
  echo "  ⏭  Skipping build (--skip-build)"
fi

# ── 5. Ensure SSM param /token-flow/anthropic_api_key ────────────────────────
echo ""
echo "🔑 Checking SSM param /token-flow/anthropic_api_key..."
TF_KEY_EXISTS=$(aws ssm get-parameter --name /token-flow/anthropic_api_key \
  --region $REGION --query 'Parameter.Name' --output text 2>/dev/null || echo "NONE")

if [[ "$TF_KEY_EXISTS" == "NONE" ]]; then
  echo "  Not found — copying from /smart-memory/anthropic_api_key..."
  SM_KEY=$(aws ssm get-parameter --name /smart-memory/anthropic_api_key \
    --region $REGION --with-decryption --query 'Parameter.Value' --output text)
  aws ssm put-parameter \
    --name /token-flow/anthropic_api_key \
    --value "$SM_KEY" \
    --type SecureString \
    --region $REGION \
    --description "Anthropic API key for token-flow (copied from /smart-memory/anthropic_api_key)"
  echo "  ✓ /token-flow/anthropic_api_key created"
else
  echo "  ✓ /token-flow/anthropic_api_key already exists"
fi

# ── 6. Resolve IAM role ARNs ─────────────────────────────────────────────────
EXEC_ROLE=$(aws iam get-role --role-name freightdawg-ecs-execution-role \
  --query 'Role.Arn' --output text)
TASK_ROLE=$(aws iam get-role --role-name freightdawg-ecs-task-role \
  --query 'Role.Arn' --output text)

# ── 7. Register ECS task definition ──────────────────────────────────────────
echo ""
echo "📋 Registering task definition '$FAMILY'..."

TASK_DEF=$(aws ecs register-task-definition \
  --region $REGION \
  --cli-input-json "$(cat "$(dirname "$0")/task-definition.json" | \
    sed "s|ACCOUNT_ID|$ACCOUNT|g" | \
    python3 -c "
import sys, json
td = json.load(sys.stdin)
td['executionRoleArn'] = td['executionRoleArn'].replace('ACCOUNT_ID', '$(echo $ACCOUNT)')
td['taskRoleArn']      = td['taskRoleArn'].replace('ACCOUNT_ID', '$(echo $ACCOUNT)')
for c in td['containerDefinitions']:
    c['image'] = c['image'].replace('ACCOUNT_ID', '$(echo $ACCOUNT)')
print(json.dumps(td))
  ")" \
  --query 'taskDefinition.{Revision:revision,Family:family}' 2>&1)

echo "  Registered: $TASK_DEF"
REVISION=$(echo "$TASK_DEF" | python3 -c "import sys,json; print(json.load(sys.stdin)['Revision'])")

# ── 8. Create or update ECS service ──────────────────────────────────────────
echo ""
echo "🚀 Deploying ECS service '$SERVICE' on cluster '$CLUSTER'..."

SERVICE_STATUS=$(aws ecs describe-services --cluster $CLUSTER --services $SERVICE \
  --region $REGION --query 'services[0].status' --output text 2>/dev/null || echo "NONE")

if [[ "$SERVICE_STATUS" == "ACTIVE" ]]; then
  echo "  Service exists — updating with force-new-deployment..."
  aws ecs update-service \
    --cluster $CLUSTER \
    --service $SERVICE \
    --task-definition $FAMILY:$REVISION \
    --force-new-deployment \
    --region $REGION \
    --query 'service.{Status:status,Desired:desiredCount}' \
    --output table
else
  echo "  Service not found — creating..."

  # Pull subnets and SG from existing freightdawg-stack
  SUBNET1=$(aws cloudformation describe-stack-resources --stack-name freightdawg-stack \
    --region $REGION \
    --query 'StackResources[?LogicalResourceId==`PublicSubnet1`].PhysicalResourceId' \
    --output text)
  SUBNET2=$(aws cloudformation describe-stack-resources --stack-name freightdawg-stack \
    --region $REGION \
    --query 'StackResources[?LogicalResourceId==`PublicSubnet2`].PhysicalResourceId' \
    --output text)
  SG=$(aws cloudformation describe-stack-resources --stack-name freightdawg-stack \
    --region $REGION \
    --query 'StackResources[?LogicalResourceId==`ECSSecurityGroup`].PhysicalResourceId' \
    --output text)

  echo "  Subnets: $SUBNET1, $SUBNET2"
  echo "  Security group: $SG"

  aws ecs create-service \
    --cluster $CLUSTER \
    --service-name $SERVICE \
    --task-definition $FAMILY:$REVISION \
    --launch-type FARGATE \
    --desired-count 1 \
    --network-configuration "awsvpcConfiguration={subnets=[$SUBNET1,$SUBNET2],securityGroups=[$SG],assignPublicIp=ENABLED}" \
    --deployment-configuration "minimumHealthyPercent=100,maximumPercent=200" \
    --region $REGION \
    --query 'service.{Status:status,Desired:desiredCount}' \
    --output table
fi

# ── 9. Wait for stable ────────────────────────────────────────────────────────
echo ""
echo "⏳ Waiting for service to stabilize (this may take ~2 min)..."
aws ecs wait services-stable --cluster $CLUSTER --services $SERVICE --region $REGION
echo "  ✓ Service stable"

# ── 10. Print public IP ───────────────────────────────────────────────────────
echo ""
TASK_ARN=$(aws ecs list-tasks --cluster $CLUSTER --service-name $SERVICE \
  --region $REGION --query 'taskArns[0]' --output text)
ENI=$(aws ecs describe-tasks --cluster $CLUSTER --tasks "$TASK_ARN" \
  --region $REGION \
  --query 'tasks[0].attachments[0].details[?name==`networkInterfaceId`].value' \
  --output text)
PUBLIC_IP=$(aws ec2 describe-network-interfaces --network-interface-ids "$ENI" \
  --region $REGION --query 'NetworkInterfaces[0].Association.PublicIp' --output text)

echo "✅ token-flow running at http://$PUBLIC_IP:8001"
echo "   Health: http://$PUBLIC_IP:8001/health"
echo "   Docs:   http://$PUBLIC_IP:8001/docs"
