#!/bin/bash

set -e

echo "=========================================="
echo "LLM Security Platform Deployment Script"
echo "=========================================="
echo ""

# Step 1: Verify AWS credentials are configured
echo "[1/5] Verifying AWS credentials..."
if ! aws sts get-caller-identity &> /dev/null; then
    echo "ERROR: AWS credentials are not configured."
    echo "Please configure AWS credentials using one of the following methods:"
    echo "  1. For EC2 instances: Attach an IAM role with required permissions"
    echo "  2. For local development: Run 'aws configure' and provide access keys"
    echo ""
    echo "Required IAM permissions:"
    echo "  - ssm:GetParameter (for /llmplatformsecurity/hftoken)"
    echo "  - sqs:GetQueueUrl (for LLmSecurityPlatformMessageQueue)"
    echo "  - dynamodb:* (for tables access)"
    exit 1
fi
echo "✓ AWS credentials verified"
echo ""

# Step 2: Export HF_TOKEN from SSM Parameter Store
echo "[2/5] Fetching HuggingFace token from SSM..."
export HF_TOKEN=$(aws ssm get-parameter \
    --name "/llmplatformsecurity/hftoken" \
    --with-decryption \
    --query "Parameter.Value" \
    --output text 2>&1)

if [ $? -ne 0 ] || [ -z "$HF_TOKEN" ]; then
    echo "ERROR: Failed to retrieve HuggingFace token from SSM."
    echo "Please ensure the parameter '/llmplatformsecurity/hftoken' exists in SSM Parameter Store."
    echo "You can create it using:"
    echo "  aws ssm put-parameter --name /llmplatformsecurity/hftoken --value 'YOUR_HF_TOKEN' --type SecureString"
    exit 1
fi
echo "✓ HuggingFace token retrieved"
echo ""

# Step 3: Export QUEUE_URL from SQS
echo "[3/5] Fetching SQS queue URL..."
export QUEUE_URL=$(aws sqs get-queue-url \
    --queue-name "LLmSecurityPlatformMessageQueue" \
    --query "QueueUrl" \
    --output text 2>&1)

if [ $? -ne 0 ] || [ -z "$QUEUE_URL" ]; then
    echo "ERROR: Failed to retrieve SQS queue URL."
    echo "Please ensure the queue 'LLmSecurityPlatformMessageQueue' exists."
    echo "Check the queue name and your AWS region configuration."
    exit 1
fi
echo "✓ SQS queue URL retrieved: $QUEUE_URL"
echo ""

# Step 4: Set AWS region
echo "[4/5] Setting AWS region..."
export AWS_REGION="us-east-1"
echo "✓ AWS region set to: $AWS_REGION"
echo ""

# Step 5: Navigate to containers directory and start services
echo "[5/5] Starting Docker containers..."
cd "$(dirname "$0")/../containers"

if ! docker-compose up -d --build; then
    echo "ERROR: Failed to start Docker containers."
    echo "Please ensure Docker and docker-compose are installed and running."
    exit 1
fi

echo ""
echo "=========================================="
echo "✓ Deployment successful!"
echo "=========================================="
echo ""
echo "Environment variables exported:"
echo "  HF_TOKEN: [REDACTED]"
echo "  QUEUE_URL: $QUEUE_URL"
echo "  AWS_REGION: $AWS_REGION"
echo ""
echo "Services started:"
echo "  - model (port 8000)"
echo "  - verifier (port 9000)"
echo "  - worker (background)"
echo ""
echo "To view logs: docker-compose logs -f"
echo "To stop services: docker-compose down"
echo "=========================================="
