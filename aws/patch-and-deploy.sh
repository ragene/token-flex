#!/bin/bash
# Patch ECS task definition with secrets from SSM and deploy.
# Called from buildspec.yml post_build phase.
set -e

REGION="${AWS_DEFAULT_REGION:-us-west-2}"
CLUSTER="${ECS_CLUSTER:-token-flow-ui}"
SERVICE="${ECS_SERVICE:-token-flow-ui}"

echo "🔐 Reading secrets from SSM..."
SECRET_KEY=$(aws ssm get-parameter --name /token-flow/secret_key --with-decryption \
  --region "$REGION" --query 'Parameter.Value' --output text 2>/dev/null || echo "")
AUTH0_DOMAIN=$(aws ssm get-parameter --name /token-flow/auth0_domain \
  --region "$REGION" --query 'Parameter.Value' --output text 2>/dev/null || echo "")
AUTH0_CLIENT_ID=$(aws ssm get-parameter --name /token-flow/auth0_client_id \
  --region "$REGION" --query 'Parameter.Value' --output text 2>/dev/null || echo "")

if [[ -z "$SECRET_KEY" ]]; then
  echo "⚠️  /token-flow/secret_key not found in SSM — SECRET_KEY will not be patched"
fi

echo "📋 Fetching current task definition..."
aws ecs describe-task-definition --task-definition "$SERVICE" \
  --region "$REGION" --query 'taskDefinition' --output json > /tmp/td.json

echo "🔧 Patching env vars..."
python3 - "$SECRET_KEY" "$AUTH0_DOMAIN" "$AUTH0_CLIENT_ID" << 'PYEOF'
import json, sys

sk, ad, ac = sys.argv[1], sys.argv[2], sys.argv[3]

with open('/tmp/td.json') as f:
    td = json.load(f)

patches = {}
if sk: patches['SECRET_KEY']      = sk
if ad: patches['AUTH0_DOMAIN']    = ad
if ac: patches['AUTH0_CLIENT_ID'] = ac

for c in td['containerDefinitions']:
    if c['name'] == 'token-flow':
        env = {e['name']: e['value'] for e in c.get('environment', [])}
        env.update(patches)
        c['environment'] = [{'name': k, 'value': v} for k, v in env.items()]
        print(f"  Patched {len(patches)} vars into token-flow container")

for key in ['taskDefinitionArn', 'revision', 'status', 'requiresAttributes',
            'placementConstraints', 'compatibilities', 'registeredAt', 'registeredBy']:
    td.pop(key, None)

with open('/tmp/td_patched.json', 'w') as f:
    json.dump(td, f)

print("  Task def patched OK")
PYEOF

echo "📝 Registering new task definition..."
NEW_ARN=$(aws ecs register-task-definition \
  --region "$REGION" \
  --cli-input-json file:///tmp/td_patched.json \
  --query 'taskDefinition.taskDefinitionArn' --output text)
echo "  ✓ New task def: $NEW_ARN"

echo "🚀 Deploying to ECS..."
aws ecs update-service \
  --cluster "$CLUSTER" \
  --service "$SERVICE" \
  --task-definition "$NEW_ARN" \
  --region "$REGION" \
  --query 'service.serviceName' --output text
echo "  ✓ ECS redeployment triggered"
