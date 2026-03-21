#!/bin/bash
# Deploy token-flow ECS task definition and update service
# Usage: ./ecs-deploy.sh [--skip-build]
#
# token-flow is the smart-memory token budget API service.
# It runs on port 8001 and uses /token-flow/anthropic_api_key from SSM.
# The ANTHROPIC_API_KEY is copied from /smart-memory/anthropic_api_key if not yet set.
set -e

REGION=us-west-2
CLUSTER=freightdawg-cluster
SERVICE=token-flow-service
FAMILY=token-flow
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
ECR="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"
IMAGE="$ECR/token-flow:latest"

echo "🔧 token-flow ECS Deploy"
echo "Account: $ACCOUNT | Region: $REGION"
echo "=================================="

# ── 1. Ensure ECR repo exists ────────────────────────────────────────────────
echo "📦 Ensuring ECR repo 'token-flow' exists..."
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

  # Build from token-flow/ root (Dockerfile is there)
  cd "$(dirname "$0")/.."

  docker build -t "$IMAGE" . 2>&1 | tail -5
  docker push "$IMAGE" 2>&1 | tail -3
  echo "  ✓ Image pushed: $IMAGE"
else
  echo "  ⏭  Skipping build (--skip-build)"
fi

# ── 3. Ensure SSM param /token-flow/anthropic_api_key ────────────────────────
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

# ── 4. Resolve IAM role ARNs ─────────────────────────────────────────────────
EXEC_ROLE=$(aws iam get-role --role-name freightdawg-ecs-execution-role \
  --query 'Role.Arn' --output text)
TASK_ROLE=$(aws iam get-role --role-name freightdawg-ecs-task-role \
  --query 'Role.Arn' --output text)

# ── 5. Register ECS task definition ──────────────────────────────────────────
echo ""
echo "📋 Registering task definition '$FAMILY'..."

TASK_DEF=$(aws ecs register-task-definition \
  --region $REGION \
  --family $FAMILY \
  --network-mode awsvpc \
  --requires-compatibilities FARGATE \
  --cpu 512 \
  --memory 1024 \
  --execution-role-arn "$EXEC_ROLE" \
  --task-role-arn "$TASK_ROLE" \
  --container-definitions "[
    {
      \"name\": \"token-flow\",
      \"image\": \"$IMAGE\",
      \"portMappings\": [{\"containerPort\": 8001, \"protocol\": \"tcp\"}],
      \"essential\": true,
      \"environment\": [
        {\"name\": \"TOKEN_FLOW_DB\",       \"value\": \"/tmp/token_flow.db\"},
        {\"name\": \"S3_BUCKET\",           \"value\": \"smart-memory\"},
        {\"name\": \"AWS_DEFAULT_REGION\",  \"value\": \"us-west-2\"},
        {\"name\": \"MEMORY_DIR\",          \"value\": \"/home/ec2-user/.openclaw/workspace/memory\"},
        {\"name\": \"WORKSPACE\",           \"value\": \"/home/ec2-user/.openclaw/workspace\"},
        {\"name\": \"PORT\",               \"value\": \"8001\"}
      ],
      \"secrets\": [
        {\"name\": \"ANTHROPIC_API_KEY\", \"valueFrom\": \"/token-flow/anthropic_api_key\"}
      ],
      \"logConfiguration\": {
        \"logDriver\": \"awslogs\",
        \"options\": {
          \"awslogs-group\": \"/ecs/freightdawg\",
          \"awslogs-region\": \"$REGION\",
          \"awslogs-stream-prefix\": \"token-flow\"
        }
      },
      \"healthCheck\": {
        \"command\": [\"CMD-SHELL\", \"curl -f http://localhost:8001/health || exit 1\"],
        \"interval\": 15,
        \"timeout\": 5,
        \"retries\": 3,
        \"startPeriod\": 30
      }
    }
  ]" \
  --query 'taskDefinition.{Revision:revision,Family:family}' 2>&1)

echo "  Registered: $TASK_DEF"
REVISION=$(echo "$TASK_DEF" | python3 -c "import sys,json; print(json.load(sys.stdin)['Revision'])")

# ── 6. Create or update ECS service ──────────────────────────────────────────
echo ""
echo "🚀 Deploying ECS service '$SERVICE'..."

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

# ── 7. Wait for stable ────────────────────────────────────────────────────────
echo ""
echo "⏳ Waiting for service to stabilize (this may take ~2 min)..."
aws ecs wait services-stable --cluster $CLUSTER --services $SERVICE --region $REGION
echo "  ✓ Service stable"

# ── 8. Print public IP ────────────────────────────────────────────────────────
echo ""
TASK_ARN=$(aws ecs list-tasks --cluster freightdawg-cluster --service-name token-flow-service \
  --region us-west-2 --query 'taskArns[0]' --output text)
ENI=$(aws ecs describe-tasks --cluster freightdawg-cluster --tasks "$TASK_ARN" \
  --region us-west-2 \
  --query 'tasks[0].attachments[0].details[?name==`networkInterfaceId`].value' \
  --output text)
PUBLIC_IP=$(aws ec2 describe-network-interfaces --network-interface-ids "$ENI" \
  --region us-west-2 --query 'NetworkInterfaces[0].Association.PublicIp' --output text)

echo "✅ token-flow running at http://$PUBLIC_IP:8001"
echo "   Health: http://$PUBLIC_IP:8001/health"
echo "   Docs:   http://$PUBLIC_IP:8001/docs"
