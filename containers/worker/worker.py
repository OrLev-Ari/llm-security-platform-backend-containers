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
challenge_scores_table = dynamodb.Table(os.getenv("CHALLENGE_SCORES_TABLE"))
global_scores_table = dynamodb.Table(os.getenv("GLOBAL_SCORES_TABLE"))

# Environment variables
QUEUE_URL = os.getenv("QUEUE_URL")
MODEL_API_URL = os.getenv("MODEL_API_URL")
VERIFIER_URL = os.getenv("VERIFIER_URL")

def close_session(session_id):
    try:
        now = datetime.utcnow().isoformat()
        
        # Get session data for scoring
        session_resp = challenge_sessions_table.get_item(Key={"session_id": session_id})
        session = session_resp.get("Item")
        
        if not session:
            print(f"[{session_id}] Session not found for scoring")
            return
        
        user_id = session.get("user_id")
        challenge_id = session.get("challenge_id")
        started_at_str = session.get("started_at")
        
        if not all([user_id, challenge_id, started_at_str]):
            print(f"[{session_id}] Missing data for scoring (user_id={user_id}, challenge_id={challenge_id})")
        
        # Update session status to completed
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
        
        # Calculate score if we have all required data
        if all([user_id, challenge_id, started_at_str]):
            # Count prompts for this session
            prompts_resp = prompts_table.query(
                KeyConditionExpression=Key("session_id").eq(session_id)
            )
            prompt_count = len(prompts_resp.get("Items", []))
            
            # Calculate time taken in minutes
            started_at = datetime.fromisoformat(started_at_str.replace('Z', '+00:00'))
            completed_at = datetime.fromisoformat(now.replace('Z', '+00:00'))
            time_seconds = (completed_at - started_at).total_seconds()
            time_minutes = time_seconds / 60.0
            
            # Apply scoring formula with free prompt and free time
            # First prompt is free, first 5 minutes are free
            billable_prompts = max(prompt_count - 1, 0)
            billable_minutes = max(time_minutes - 5, 0)
            
            prompt_penalty = min(billable_prompts * 2, 50)
            time_penalty = min(billable_minutes * 0.5, 30)
            final_score = int(100 - prompt_penalty - time_penalty)  # Round down to integer
            
            print(f"[{session_id}] Score calculation: prompts={prompt_count} (billable={billable_prompts}), time={time_minutes:.2f}m (billable={billable_minutes:.2f}m), score={final_score}")
            
            # Update leaderboard scores
            update_leaderboard_scores(
                user_id=user_id,
                challenge_id=challenge_id,
                score=final_score,
                prompt_count=prompt_count,
                time_seconds=int(time_seconds),
                completed_at=now,
                session_id=session_id
            )

    except ClientError as e:
        print(f"[{session_id}] Failed to close session: {e}")
    except Exception as e:
        print(f"[{session_id}] Error during session closure: {e}")

# -------------------------------------------------
# Update leaderboard scores
# -------------------------------------------------
def update_leaderboard_scores(user_id, challenge_id, score, prompt_count, time_seconds, completed_at, session_id):
    """
    Updates ChallengeScoresTable and GlobalScoresTable with new score.
    Only updates if new score is better than existing score (or if no existing score).
    """
    try:
        print(f"[{session_id}] Updating leaderboard for user={user_id}, challenge={challenge_id}, score={score:.2f}")
        
        # Get existing score for this user/challenge combination
        existing_score_resp = challenge_scores_table.get_item(
            Key={"user_id": user_id, "challenge_id": challenge_id}
        )
        existing_item = existing_score_resp.get("Item")
        existing_score = existing_item.get("score") if existing_item else None
        
        # Only update if new score is better or no existing score
        if existing_score is None or score > existing_score:
            score_delta = score - (existing_score if existing_score else 0)
            is_new_challenge = existing_score is None
            
            print(f"[{session_id}] Score improvement: old={existing_score}, new={score:.2f}, delta={score_delta:.2f}")
            
            # Update ChallengeScoresTable
            challenge_scores_table.put_item(
                Item={
                    "user_id": user_id,
                    "challenge_id": challenge_id,
                    "score": int(score),  # Convert to int for cleaner storage
                    "prompt_count": prompt_count,
                    "time_seconds": time_seconds,
                    "completed_at": completed_at,
                    "session_id": session_id
                }
            )
            print(f"[{session_id}] ChallengeScoresTable updated")
            
            # Update GlobalScoresTable
            # Get current global score
            global_score_resp = global_scores_table.get_item(Key={"user_id": user_id})
            global_item = global_score_resp.get("Item")
            
            if global_item:
                # User exists, update total_score
                current_total = global_item.get("total_score", 0)
                current_challenges = global_item.get("challenges_completed", 0)
                new_total = current_total + score_delta
                new_challenges = current_challenges + (1 if is_new_challenge else 0)
                
                global_scores_table.put_item(
                    Item={
                        "user_id": user_id,
                        "total_score": int(new_total),
                        "challenges_completed": new_challenges,
                        "last_updated": completed_at,
                        "leaderboard_type": "GLOBAL"  # Fixed partition key for GSI
                    }
                )
                print(f"[{session_id}] GlobalScoresTable updated: total={new_total:.2f}, challenges={new_challenges}")
            else:
                # New user, create initial record
                global_scores_table.put_item(
                    Item={
                        "user_id": user_id,
                        "total_score": int(score),
                        "challenges_completed": 1,
                        "last_updated": completed_at,
                        "leaderboard_type": "GLOBAL"  # Fixed partition key for GSI
                    }
                )
                print(f"[{session_id}] GlobalScoresTable created for new user: total={score:.2f}")
        else:
            print(f"[{session_id}] Score not improved (existing={existing_score}, new={score:.2f}), skipping update")
    
    except ClientError as e:
        print(f"[{session_id}] Failed to update leaderboard: {e}")
    except Exception as e:
        print(f"[{session_id}] Error updating leaderboard: {e}")

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
            timeout=60
        )
        print("--------------------------------------------------")
        print(f"[{session_id}/{prompt_id}] VERIFIER INPUT:")
        print(f"System Prompt: {system_prompt}")
        print(f"Model Output: {model_output}")
        print("--------------------------------------------------")

        verifier_latency = round(time.time() - verifier_start, 3)
        verifier_output = verifier_resp.json().get("verdict", "UNVERIFIED")

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