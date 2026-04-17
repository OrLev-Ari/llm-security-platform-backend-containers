# EC2 Deployment Guide

## Quick Start Overview

This guide walks you through deploying the LLM Security Platform on AWS EC2. The process involves:

1. **Creating an EC2 instance** with the required specifications
2. **Installing prerequisites** (Docker, AWS CLI, Git)
3. **Configuring AWS credentials** and permissions
4. **Cloning the repository** to the EC2 instance
5. **Running the automated deployment script** to start all services

---

## Step 1: Create EC2 Instance

### Instance Specifications
- **Instance Type**: `m7i-flex.large` (or larger)
- **Minimum RAM**: **8GB** (for default Llama-3.2-1B model) or **16GB+** (for recommended Ministral-8B)
- **Operating System**: Amazon Linux 2023 or Ubuntu 22.04 LTS
- **Storage**: At least 20GB EBS volume
- **Security Group**: Allow inbound SSH (port 22) from your IP

### IAM Role Setup (Recommended)

Create an IAM role with the following permissions and attach it to your EC2 instance:

**Required Permissions:**
- `ssm:GetParameter` - to retrieve HuggingFace token from Parameter Store
- `sqs:GetQueueUrl` - to retrieve SQS queue URL
- `dynamodb:GetItem`, `dynamodb:PutItem`, `dynamodb:UpdateItem`, `dynamodb:Query` - for DynamoDB table access

**Example IAM Policy:**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ssm:GetParameter"
      ],
      "Resource": "arn:aws:ssm:us-east-1:*:parameter/llmplatformsecurity/hftoken"
    },
    {
      "Effect": "Allow",
      "Action": [
        "sqs:GetQueueUrl",
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
        "dynamodb:Query"
      ],
      "Resource": [
        "arn:aws:dynamodb:us-east-1:*:table/PromptsTable",
        "arn:aws:dynamodb:us-east-1:*:table/ChallengeSessionsTable",
        "arn:aws:dynamodb:us-east-1:*:table/ChallengesTable"
      ]
    }
  ]
}
```

### Launch the Instance

```bash
# Example using AWS CLI
aws ec2 run-instances \
    --image-id ami-xxxxxxxx \
    --instance-type m7i-flex.large \
    --iam-instance-profile Name=YourIAMRoleName \
    --key-name YourKeyPair \
    --security-group-ids sg-xxxxxxxx \
    --block-device-mappings '[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":50}}]'
```

Or use the AWS Console to launch the instance manually.

---

## Step 2: Connect to EC2 Instance

SSH into your newly created instance:

```bash
ssh -i /path/to/your-key.pem ec2-user@<EC2_PUBLIC_IP>
```

For Ubuntu instances, use `ubuntu@<EC2_PUBLIC_IP>` instead.

---

## Step 3: Install Prerequisites

### Update System Packages

**Amazon Linux 2023:**
```bash
sudo yum update -y
```

**Ubuntu:**
```bash
sudo apt update && sudo apt upgrade -y
```

### Install Docker

**Amazon Linux 2023:**
```bash
sudo yum install -y docker
sudo systemctl start docker
sudo systemctl enable docker
sudo usermod -a -G docker ec2-user
```

**Ubuntu:**
```bash
sudo apt install -y docker.io
sudo systemctl start docker
sudo systemctl enable docker
sudo usermod -a -G docker ubuntu
```

**Log out and back in** for group membership to take effect:
```bash
exit
# SSH back in
ssh -i /path/to/your-key.pem ec2-user@<EC2_PUBLIC_IP>
```

Verify Docker installation:
```bash
docker --version
docker ps  # Should work without sudo
```

### Install Docker Compose

```bash
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose
docker-compose --version
```

### Install Git

**Amazon Linux 2023:**
```bash
sudo yum install -y git
```

**Ubuntu:**
```bash
sudo apt install -y git
```

### Install/Verify AWS CLI

AWS CLI is pre-installed on Amazon Linux. For Ubuntu or to update:

```bash
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
sudo apt install unzip -y
unzip awscliv2.zip
sudo ./aws/install
aws --version
```

---

## Step 4: Configure AWS Credentials

### Option 1: IAM Role (Recommended)

If you attached an IAM role to your EC2 instance during creation, verify it's working:

```bash
aws sts get-caller-identity
```

You should see your instance role information. **No additional configuration needed.**

### Option 2: Manual Credentials

If not using an IAM role, configure AWS credentials:

```bash
aws configure
```

Enter:
- AWS Access Key ID
- AWS Secret Access Key
- Default region: `us-east-1`
- Default output format: `json`

---

## Step 5: Set Up HuggingFace Token in SSM

The system requires a HuggingFace token stored in AWS Systems Manager Parameter Store.

### Create the SSM Parameter

**From your local machine** (with appropriate AWS permissions):

```bash
aws ssm put-parameter \
    --name "/llmplatformsecurity/hftoken" \
    --value "YOUR_HUGGINGFACE_TOKEN" \
    --type "SecureString" \
    --region us-east-1
```

Replace `YOUR_HUGGINGFACE_TOKEN` with your actual HuggingFace token (get one from https://huggingface.co/settings/tokens).

### Verify the Parameter Exists

From the EC2 instance:

```bash
aws ssm get-parameter \
    --name /llmplatformsecurity/hftoken \
    --with-decryption \
    --query Parameter.Value \
    --output text
```

---

## Step 6: Clone the Repository

```bash
cd ~
git clone https://github.com/OrLev-Ari/llm-security-platform-backend-containers.git
cd llm-security-platform-backend-containers
```

---

## Step 7: Copy GGUF Model to EC2

**Important:** The GGUF model file is not included in the repository and must be copied separately.

### From Your Local Machine

The model container expects a GGUF model file at `containers/model/models/qwen2.5-0.5b-instruct-q3_k_m.gguf`. ( you can switch it to a different model of course )

**Copy the model file using SCP:**

```bash
# From your local machine (replace paths and IP accordingly)
scp -i /path/to/your-key.pem \
    /path/to/qwen2.5-0.5b-instruct-q3_k_m.gguf \
    ec2-user@<EC2_PUBLIC_IP>:~/llm-security-platform-backend-containers/containers/model/models/
```

**For Ubuntu instances**, replace `ec2-user` with `ubuntu`.

**Alternative - if you need to create the directory first:**

```bash
# SSH into EC2
ssh -i /path/to/your-key.pem ec2-user@<EC2_PUBLIC_IP>

# Create the models directory
mkdir -p ~/llm-security-platform-backend-containers/containers/model/models

# Exit and copy from local machine
exit

# Copy the file
scp -i /path/to/your-key.pem \
    /path/to/qwen2.5-0.5b-instruct-q3_k_m.gguf \
    ec2-user@<EC2_PUBLIC_IP>:~/llm-security-platform-backend-containers/containers/model/models/
```

**Verify the file was copied:**

```bash
# SSH back into EC2
ssh -i /path/to/your-key.pem ec2-user@<EC2_PUBLIC_IP>

# Check the file exists
ls -lh ~/llm-security-platform-backend-containers/containers/model/models/
```

You should see the GGUF file listed.

---

## Step 8: Run the Deployment Script

Now you're ready to deploy!

```bash
cd EC2
chmod +x deploy.sh
./deploy.sh
```

### What the Script Does

The deployment script automates the following:

1. ✓ Verifies AWS credentials are configured
2. ✓ Retrieves HuggingFace token from SSM Parameter Store (`/llmplatformsecurity/hftoken`)
3. ✓ Retrieves SQS queue URL using AWS CLI
4. ✓ Exports environment variables (`HF_TOKEN`, `QUEUE_URL`, `AWS_REGION`)
5. ✓ Navigates to `containers/` directory
6. ✓ Runs `docker-compose up -d --build` to start all services

The script will **fail fast** with clear error messages if any step fails.

### Expected Output

```
==========================================
LLM Security Platform Deployment Script
==========================================

[1/5] Verifying AWS credentials...
✓ AWS credentials verified

[2/5] Fetching HuggingFace token from SSM...
✓ HuggingFace token retrieved

[3/5] Fetching SQS queue URL...
✓ SQS queue URL retrieved: https://sqs.us-east-1.amazonaws.com/...

[4/5] Setting AWS region...
✓ AWS region set to: us-east-1

[5/5] Starting Docker containers...
Creating network "containers_default" ...
Building model...
Building verifier...
Building worker...
...
✓ Deployment successful!
==========================================
```

---

## Step 9: Verify Deployment

### Check Running Containers

```bash
cd ~/llm-security-platform-backend-containers/containers
docker ps
```

You should see three containers running:
- `model` (port 8000)
- `verifier` (port 9000)
- `worker` (background)

### View Logs

```bash
docker-compose logs -f
```

Press `Ctrl+C` to exit logs.

### Test Model API

```bash
curl -X POST http://localhost:8000/generate \
    -H "Content-Type: application/json" \
    -d '{"prompt": "Hello, how are you?"}'
```

---

## Managing the Deployment

### Stop Services

```bash
cd ~/llm-security-platform-backend-containers/containers
docker-compose down
```

### Restart Services

```bash
docker-compose up -d
```

### Rebuild After Code Changes

```bash
docker-compose up -d --build
```

### View Individual Container Logs

```bash
docker-compose logs -f model
docker-compose logs -f verifier
docker-compose logs -f worker
```

---

## Manual Deployment (Alternative)

If you prefer not to use the automated script, deploy manually:

### Export Environment Variables

```bash
export HF_TOKEN=$(aws ssm get-parameter \
    --name /llmplatformsecurity/hftoken \
    --with-decryption \
    --query Parameter.Value \
    --output text)

export QUEUE_URL=$(aws sqs get-queue-url \
    --queue-name LLmSecurityPlatformMessageQueue \
    --query QueueUrl \
    --output text)

export AWS_REGION=us-east-1
```

### Start Docker Containers

```bash
cd ~/llm-security-platform-backend-containers/containers
docker-compose up -d --build
```

---

## Prerequisites Summary
- **Instance Type**: `m7i-flex.large` (or larger)
- **Minimum RAM**: **20GB** (required for Mistral-3-8B judge model)
- **Pre-installed Software**: Docker and docker-compose

### AWS Configuration

Before deploying, ensure AWS credentials are properly configured on your EC2 instance:

#### Option 1: IAM Role (Recommended for EC2)
Attach an IAM role to your EC2 instance with the following permissions:
- `ssm:GetParameter` - to retrieve HuggingFace token from Parameter Store
- `sqs:GetQueueUrl` - to retrieve SQS queue URL
- `dynamodb:*` - to access DynamoDB tables (PromptsTable, ChallengeSessionsTable, ChallengesTable)

#### Option 2: AWS CLI Configuration
If not using an IAM role, configure credentials manually:
```bash
aws configure
```
Enter your AWS Access Key ID, Secret Access Key, and default region (`us-east-1`).

### HuggingFace Token Setup

The system requires a HuggingFace token to download the Mistral-3-8B model. This token must be stored in AWS Systems Manager Parameter Store:

**Parameter Name**: `/llmplatformsecurity/hftoken`

If the parameter doesn't exist, create it:
```bash
aws ssm put-parameter \
    --name "/llmplatformsecurity/hftoken" \
    --value "YOUR_HUGGINGFACE_TOKEN" \
    --type "SecureString" \
    --region us-east-1
```

---

## Troubleshooting

### "AWS credentials not configured"
- Verify IAM role is attached: `aws sts get-caller-identity`
- Or run `aws configure` to set up credentials manually

### "Failed to retrieve HuggingFace token from SSM"
- Verify parameter exists: `aws ssm get-parameter --name /llmplatformsecurity/hftoken`
- Check IAM permissions include `ssm:GetParameter`
- Create the parameter if missing (see Step 5)

### "Failed to retrieve SQS queue URL"
- Verify queue exists: `aws sqs list-queues`
- Check queue name is exactly `LLmSecurityPlatformMessageQueue`
- Verify region is correct (`us-east-1`)

### "Docker: permission denied"
- You need to log out and back in after adding user to docker group
- Or run: `newgrp docker`

### Container Out of Memory
- Check which model you're using (default Llama-3.2-1B or Ministral-8B)
- Llama-3.2-1B needs ~6GB RAM, Ministral-8B needs ~16-20GB RAM
- Verify available memory: `free -h`
- Consider adding swap space (see Architecture Notes below)
- For Ministral-8B, upgrade to instance with 16GB+ RAM

---

## Architecture Notes

### Verifier Model

**Default Configuration (Low Memory):**  
The system currently uses **Llama-3.2-1B-Instruct** (`meta-llama/Llama-3.2-1B-Instruct`) as the security judge. This lightweight model requires only ~4-6GB RAM and is suitable for testing and limited-resource environments.

**Recommended Production Configuration:**  
For production deployments with adequate resources (16GB+ RAM), use **Ministral-8B-Instruct** (`mistralai/Ministral-8B-Instruct-2410`) for significantly better accuracy and reliability.

**How to switch to Ministral-8B:**

1. Ensure your instance has at least 16GB RAM (e.g., `m7i-flex.large` or larger)
2. Edit `containers/verifier/app.py` and change the `MODEL_NAME`:
   ```python
   # Change from:
   MODEL_NAME = "meta-llama/Llama-3.2-1B-Instruct"
   
   # To:
   MODEL_NAME = "mistralai/Ministral-8B-Instruct-2410"
   ```
3. **Important:** Modify the chat template configuration:
   - The current implementation uses Llama's chat template format
   - Ministral may use different formatting - test and adjust the `apply_chat_template` section if needed
   - You may need to remove or modify the chat template code depending on Ministral's requirements
   
4. Rebuild and restart containers:
   ```bash
   cd ~/llm-security-platform-backend-containers/containers
   docker-compose down
   docker-compose up -d --build
   ```

**Timeout Configuration:**

The worker has a **60-second timeout** for verifier responses to accommodate LLM inference time. This works well for both models, but if you experience timeout errors:

- Monitor logs: `docker-compose logs -f worker verifier`
- If verifier takes longer than 60s, edit `containers/worker/worker.py`:
  ```python
  # Find this line and increase the timeout value:
  timeout=60  # Change to 90 or 120 if needed
  ```
- Rebuild: `docker-compose up -d --build`

**Verification Logic:**
- Judge receives **system prompt** + **model response** (user prompt excluded for security)
- Returns JSON verdict: `{"result": "YES"}` (violation) or `{"result": "NO"}` (safe)
- `YES` → marked as `JAILBREAK`, session closed with `completed_at` timestamp
- `NO` → marked as `SAFE`, session continues
- Parse errors → marked as `UNVERIFIED`

### Memory Considerations

**Current model (Llama-3.2-1B)**: Requires ~4-6GB RAM  
**Recommended model (Ministral-8B)**: Requires ~16-20GB RAM  

If using Ministral-8B on an instance with limited physical RAM, configure swap space:

```bash
sudo fallocate -l 8G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
```

Make swap persistent by adding to `/etc/fstab`:
```
/swapfile none swap sw 0 0
```

Verify swap is active:
```bash
free -h
```

---

## Security Best Practices

1. **Use IAM roles** instead of hardcoded credentials
2. **Restrict security groups** - only allow SSH from your IP
3. **Keep SSM parameters encrypted** - use SecureString type
4. **Regularly update** - run `docker-compose pull && docker-compose up -d --build`
5. **Monitor logs** - check for suspicious activity in worker logs
6. **Rotate credentials** - periodically update HuggingFace token

---

## Additional Resources

- **HuggingFace Token**: https://huggingface.co/settings/tokens
- **AWS SSM Documentation**: https://docs.aws.amazon.com/systems-manager/latest/userguide/systems-manager-parameter-store.html
- **Docker Documentation**: https://docs.docker.com/
- **Repository**: https://github.com/OrLev-Ari/llm-security-platform-backend-containers