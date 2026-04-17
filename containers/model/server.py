from fastapi import FastAPI
from pydantic import BaseModel
from llama_cpp import Llama

# Load model from container path
llm = Llama(
    model_path="/app/models/qwen2.5-0.5b-instruct-q3_k_m.gguf",
    n_ctx=2048,
    n_gpu_layers=-1,
    verbose=False
)

app = FastAPI()

class Request(BaseModel):
    prompt: str
    max_tokens: int = 100

@app.post("/generate")
def generate(req: Request):
    output = llm(
        req.prompt,
        max_tokens=req.max_tokens,
        stop=["Question:", "\n"],
        echo=False
    )
    return {"response": output["choices"][0]["text"]}
