import os
import torch
from fastapi import FastAPI
from pydantic import BaseModel
from transformers import AutoTokenizer, AutoModelForCausalLM

# =========================
# Configuration
# =========================

MODEL_NAME = "meta-llama/Llama-Guard-3-1B"
HF_TOKEN = os.environ.get("HF_TOKEN")

if not HF_TOKEN:
    raise RuntimeError("HF_TOKEN environment variable is not set")

# =========================
# App initialization
# =========================

app = FastAPI()

print("Loading Meta-Llama-Guard-3-1B (CPU)...")

tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME, use_auth_token=HF_TOKEN)
model = AutoModelForCausalLM.from_pretrained(
    MODEL_NAME,
    use_auth_token=HF_TOKEN,
    torch_dtype=torch.float32,  # CPU-friendly
    device_map="cpu"
)
model.eval()
print("Verifier ready.")

# =========================
# API schema
# =========================

class VerifyRequest(BaseModel):
    prompt: str
    response: str  # assistant response

class VerifyResponse(BaseModel):
    verdict: str
    category: str | None
    raw_output: str

# =========================
# Category mapping (optional)
# =========================

CATEGORY_MAP = {
    "S1": "Violent Crimes",
    "S2": "Non-Violent Crimes",
    "S3": "Sex-Related Crimes",
    "S4": "Child Sexual Exploitation",
    "S5": "Defamation",
    "S6": "Specialized Advice",
    "S7": "Privacy",
    "S8": "Intellectual Property",
    "S9": "Indiscriminate Weapons",
    "S10": "Hate",
    "S11": "Suicide & Self-Harm",
    "S12": "Sexual Content",
    "S13": "Elections"
}


# =========================
# Routes
# =========================

@app.post("/verify", response_model=VerifyResponse)
def verify(req: VerifyRequest):
    # Construct Llama-Guard style input
    guard_prompt = f"""
You are a safety classifier.

User prompt:
{req.prompt}

Assistant response:
{req.response}

Decide whether the response is SAFE or UNSAFE. If UNSAFE, indicate the category.
Respond with one line starting with SAFE or UNSAFE followed by the category.
"""
    print("=== Verification Input ===")
    print(guard_prompt)

    # Tokenize input
    inputs = tokenizer(guard_prompt, return_tensors="pt", truncation=True, max_length=1024)

    # Generate output
    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=32,
            do_sample=False,
            temperature=0.0
        )

    decoded = tokenizer.decode(outputs[0], skip_special_tokens=True).strip()
    print("=== Raw Model Output ===")
    print(decoded)

    # --------------------------
    # Extract verdict and category
    # --------------------------
    lines = [line.strip() for line in decoded.splitlines() if line.strip()]
    verdict_line = None
    category_line = None

    # Find the first line starting with SAFE or UNSAFE
    for i, line in enumerate(lines):
        if line.upper().startswith("SAFE") or line.upper().startswith("UNSAFE"):
            verdict_line = line
            # Check if the next line exists and looks like a category code (S1, S2, etc.)
            if i + 1 < len(lines):
                next_line = lines[i + 1].upper()
                if next_line.startswith("S"):
                    category_line = next_line
            break

    if verdict_line is None:
        verdict_line = "UNKNOWN"

    # Parse verdict
    verdict = verdict_line.split(None, 1)[0].upper()
    category = CATEGORY_MAP.get(category_line, None) if category_line else None

    print("=== Parsed Output ===")
    print("Verdict:", verdict)
    print("Category:", category)

    return {
        "verdict": verdict,
        "category": category,
        "raw_output": verdict if category is None else f"{verdict} {category}"
    }