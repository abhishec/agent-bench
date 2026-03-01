from __future__ import annotations
import os
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse

from src.executor import handle_task
from src import benchmark_intelligence


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load benchmark intelligence (proven tool sequences) on startup
    loaded = benchmark_intelligence.load_intelligence()
    if loaded:
        print("[purple-agent] BenchmarkIntelligence loaded successfully", flush=True)
    else:
        print("[purple-agent] BenchmarkIntelligence not loaded (no training data or S3 unavailable)", flush=True)
    yield


app = FastAPI(title="BrainOS Purple Agent", version="1.0.0", lifespan=lifespan)

AGENT_CARD = {
    "name": "BrainOS Purple Agent",
    "description": "Thin BrainOS connector that solves business tasks using BrainOS (with Claude fallback).",
    "version": "1.0.0",
    "url": os.getenv("PURPLE_AGENT_CARD_URL", "http://localhost:9010"),
    "capabilities": {"streaming": False, "tools": True},
    "skills": [{"id": "general", "name": "General Business Task Solver"}],
}


@app.get("/.well-known/agent-card.json")
async def agent_card():
    return JSONResponse(AGENT_CARD)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "agent": "purple-brainos-connector",
        "has_benchmark_intelligence": benchmark_intelligence.is_loaded(),
    }


@app.post("/")
async def a2a_handler(request: Request):
    body = await request.json()

    if body.get("method") != "tasks/send":
        raise HTTPException(400, "Only tasks/send method supported")

    params = body.get("params", {})
    task_id = params.get("id", str(uuid.uuid4()))
    message = params.get("message", {})
    metadata = params.get("metadata", {})

    task_text = "".join(p.get("text", "") for p in message.get("parts", []))
    policy_doc = metadata.get("policy_doc", "")
    tools_endpoint = metadata.get("tools_endpoint", "")
    session_id = metadata.get("session_id", task_id)

    answer = await handle_task(
        task_text=task_text,
        policy_doc=policy_doc,
        tools_endpoint=tools_endpoint,
        task_id=task_id,
        session_id=session_id,
    )

    return {
        "jsonrpc": "2.0",
        "result": {
            "id": task_id,
            "status": {"state": "completed"},
            "artifacts": [{"parts": [{"text": answer}]}],
        },
    }
