import os
import time
import json
import boto3
import requests
from datetime import datetime
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key
import functools

# Force print flush for immediate logs
print = functools.partial(print, flush=True)

# AWS clients
sqs = boto3.client("sqs", region_name=os.getenv("AWS_REGION"))
dynamodb = boto3.resource("dynamodb", region_name=os.getenv("AWS_REGION"))

prompts_table = dynamodb.Table(os.getenv("PROMPTS_TABLE"))
challenge_sessions_table = dynamodb.Table(os.getenv("CHALLENGE_SESSIONS_TABLE"))
challenges_table = dynamodb.Table(os.getenv("CHALLENGES_TABLE"))

# Environment variables
QUEUE_URL = os.getenv("QUEUE_URL")
MODEL_API_URL = os.getenv("MODEL_API_URL")
VERIFIER_URL = os.getenv("VERIFIER_URL")

def close_session(session_id):
    try:
        now = datetime.utcnow().isoformat()
        challenge_sessions_table.update_item(
            Key={"session_id": session_id},
            UpdateExpression="SET #status = :s, completed_at = :t",
            ExpressionAttributeNames={
                "#status": "status"
            },
            ExpressionAttributeValues={
                ":s": "completed",
                ":t": now
            }
        )
        print(f"[{session_id}] Session COMPLETED at {now}")

    except ClientError as e:
        print(f"[{session_id}] Failed to close session: {e}")

# -------------------------------------------------
# Fetch system prompt via session -> challenge
# -------------------------------------------------
def get_system_prompt(session_id):
    try:
        session_resp = challenge_sessions_table.get_item(
            Key={"session_id": session_id}
        )
        session = session_resp.get("Item")
        if not session:
            print(f"[{session_id}] Session not found")
            return ""

        challenge_id = session.get("challenge_id")
        if not challenge_id:
            print(f"[{session_id}] Missing challenge_id")
            return ""

        challenge_resp = challenges_table.get_item(
            Key={"challenge_id": challenge_id}
        )
        challenge = challenge_resp.get("Item")
        if not challenge:
            print(f"[{session_id}] Challenge not found: {challenge_id}")
            return ""

        return challenge.get("system_prompt", "").strip()

    except Exception as e:
        print(f"[{session_id}] ERROR fetching system prompt:", e)
        return ""
    
# -------------------------------------------------
# Get conversation history
# -------------------------------------------------
def get_full_conversation(session_id, prompt_id):
    print(f"[{session_id}/{prompt_id}] Rebuilding conversation history")

    try:
        resp = prompts_table.query(
            KeyConditionExpression=Key("session_id").eq(session_id),
            ScanIndexForward=True
        )
        items = resp.get("Items", [])
        print(f"[{session_id}/{prompt_id}] Retrieved {len(items)} items")

        conversation = ""
        for item in items:
            if item["prompt_id"] == prompt_id:
                continue

            conversation += f"User: {item.get('prompt_text', '')}\n"
            if item.get("response_text") and item["response_text"] != "None":
                conversation += f"Assistant: {item['response_text']}\n"

        return conversation

    except Exception as e:
        print(f"[{session_id}/{prompt_id}] ERROR reading conversation:", e)
        return ""

# --- Update DynamoDB ---
def update_prompts_table(session_id, prompt_id, model_output, verifier_output, timestamp):
    print(f"[{session_id}/{prompt_id}] Updating DynamoDB")

    try:
        prompts_table.update_item(
            Key={
                "session_id": session_id,
                "timestamp": timestamp
            },
            UpdateExpression="""
                SET response_text = :r,
                    verified_response = :v,
                    sent_to_ui = :u
            """,
            ExpressionAttributeValues={
                ":r": model_output,
                ":v": verifier_output,
                ":u": False
            }
        )
        print(f"[{session_id}/{prompt_id}] DynamoDB update successful")

    except ClientError as e:
        print(f"[{session_id}/{prompt_id}] DynamoDB update FAILED:", e)

# --- Process each SQS message ---
def process_message(body):
    session_id = body["session_id"]
    prompt_id = body["prompt_id"]
    prompt = body["prompt"]
    timestamp = body["timestamp"]

    print(f"[{session_id}/{prompt_id}] Processing message")

    # --- Build prompt with conversation history ---
    system_prompt = get_system_prompt(session_id)
    conversation_history = get_full_conversation(session_id, prompt_id)
    full_prompt = ""
    if system_prompt:
        full_prompt += f"System Prompt: {system_prompt}" + "\n\n"
    full_prompt += conversation_history
    full_prompt += f"User: {prompt}\nAssistant: "

    print("--------------------------------------------------")
    print(f"[{session_id}/{prompt_id}] FULL PROMPT TO MODEL:")
    print(full_prompt)
    print("--------------------------------------------------")

    # --- Call model API ---
    model_start = time.time()
    model_resp = requests.post(MODEL_API_URL, json={"prompt": full_prompt})
    model_latency = round(time.time() - model_start, 3)

    print(f"[{session_id}/{prompt_id}] Model responded "
          f"(status={model_resp.status_code}, {model_latency}s)")

    model_output = model_resp.json().get("response", "")

    # --- Call verifier API ---
    try:
        print(f"[{session_id}/{prompt_id}] Sending response to verifier")
        
        verifier_start = time.time()

        verifier_resp = requests.post(
            VERIFIER_URL,
            json={"system_prompt": system_prompt, "response": model_output},
            timeout=20
        )
        print("--------------------------------------------------")
        print(f"[{session_id}/{prompt_id}] VERIFIER INPUT:")
        print(f"System Prompt: {system_prompt}")
        print(f"Model Output: {model_output}")
        print("--------------------------------------------------")

        verifier_latency = round(time.time() - verifier_start, 3)
        verifier_output = verifier_resp.json().get("raw_output", "UNVERIFIED")

        print(f"[{session_id}/{prompt_id}] Verifier output={verifier_output} "
              f"({verifier_latency}s)")

        if verifier_output == "JAILBREAK":
            close_session(session_id)
    except Exception as e:
        print(f"[{session_id}/{prompt_id}] Verifier ERROR:", e)
        verifier_output = "UNVERIFIED"

    # --- Update DynamoDB ---
    update_prompts_table(
        session_id,
        prompt_id,
        model_output,
        verifier_output,
        timestamp
    )

# --- Main loop ---
def main():
    print("Worker starting...")
    print(f"AWS_REGION={os.getenv('AWS_REGION')}")
    print(f"QUEUE_URL set={bool(QUEUE_URL)}")
    print(f"MODEL_API_URL={MODEL_API_URL}")
    print(f"VERIFIER_URL={VERIFIER_URL}")

    print("Polling SQS...")

    while True:
        try:
            resp = sqs.receive_message(
                QueueUrl=QUEUE_URL,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=10
            )

            messages = resp.get("Messages", [])
            if not messages:
                continue

            msg = messages[0]
            body = json.loads(msg["Body"])

            print(f"Received SQS message {msg.get('MessageId')}")

            process_message(body)

            sqs.delete_message(
                QueueUrl=QUEUE_URL,
                ReceiptHandle=msg["ReceiptHandle"]
            )
            print("SQS message deleted")

        except Exception as e:
            print("ERROR polling SQS:", e)

        time.sleep(1)

if __name__ == "__main__":
    main()