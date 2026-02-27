"""
Green Agent — FastAPI server.
Exposes: GET /.well-known/agent-card.json, POST / (A2A), POST /mcp, GET /mcp/tools, GET /health
         POST /benchmark  — run a benchmark task against purple and return scores
"""
from __future__ import annotations
import os
import socket
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.mcp_server import call_tool, get_tools_for_session
from src.scenarios import SCENARIO_REGISTRY


def _own_url() -> str:
    """Return this container's reachable URL for tool calls.
    Uses GREEN_AGENT_HOST_URL env var if set, otherwise auto-detects private IP."""
    override = os.getenv("GREEN_AGENT_HOST_URL", "")
    if override:
        return override.rstrip("/")
    try:
        # Gets the primary outbound interface IP (works in ECS Fargate)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        port = os.getenv("PORT", "9009")
        return f"http://{ip}:{port}"
    except Exception:
        return "http://localhost:9009"

app = FastAPI(title="GreenBenchmark Agent", version="1.0.0")


# ── Agent Card ──────────────────────────────────────────────────────────────
AGENT_CARD = {
    "name": "GreenBenchmark Agent",
    "description": "Benchmark orchestrator that issues tasks to AI agents and scores their responses across 15 business scenarios.",
    "version": "1.0.0",
    "url": "http://localhost:9009",
    "capabilities": {"streaming": False, "tools": True},
    "skills": [
        {"id": task_id, "name": task_id.replace("_", " ").title()}
        for task_id in SCENARIO_REGISTRY
    ],
}


@app.get("/.well-known/agent-card.json")
async def agent_card():
    return JSONResponse(AGENT_CARD)


@app.get("/health")
async def health():
    from src.mcp_server import TOOL_SCHEMAS
    return {"status": "ok", "agent": "green-benchmark", "scenarios": len(SCENARIO_REGISTRY), "tool_schema_count": len(TOOL_SCHEMAS)}


# ── MCP Tool Server ─────────────────────────────────────────────────────────
class MCPRequest(BaseModel):
    tool: str
    params: dict[str, Any] = {}
    session_id: str = ""


@app.post("/mcp")
async def mcp_call(req: MCPRequest):
    session_id = req.session_id or str(uuid.uuid4())
    result = await call_tool(req.tool, req.params, session_id)
    return result


@app.get("/mcp/tools")
async def mcp_tools(session_id: str = ""):
    return get_tools_for_session(session_id or "")


# ── Benchmark Runner ─────────────────────────────────────────────────────────
class BenchmarkRequest(BaseModel):
    task_id: str
    purple_url: str
    difficulty: str = "none"


@app.post("/benchmark")
async def run_benchmark(req: BenchmarkRequest):
    """
    Trigger a benchmark assessment from within the green-agent container.
    Green-agent auto-detects its own IP to pass as tools_endpoint to purple,
    so the entire round-trip stays within AWS (no local machine needed).
    """
    from src.task_manager import run_assessment

    session_id = str(uuid.uuid4())
    host_url = _own_url()

    result = await run_assessment(
        task_id=req.task_id,
        purple_agent_url=req.purple_url,
        green_agent_url=host_url,
        difficulty=req.difficulty,
        session_id=session_id,
    )

    scores = result.score.summary()
    return {
        "task_id": req.task_id,
        "session_id": session_id,
        "green_agent_url": host_url,
        "purple_url": req.purple_url,
        "answer": result.answer[:500] if result.answer else "",
        "tool_calls_count": len(result.tool_calls),
        "scores": scores,
        "error": result.error,
    }


# ── A2A Receiver ─────────────────────────────────────────────────────────────
@app.post("/")
async def a2a_handler(request: Request):
    body = await request.json()

    if body.get("method") != "tasks/send":
        raise HTTPException(400, "Only tasks/send method is supported")

    params = body.get("params", {})
    task_id = params.get("id", str(uuid.uuid4()))
    message = params.get("message", {})
    metadata = params.get("metadata", {})
    session_id = metadata.get("session_id", task_id)

    task_text = ""
    for part in message.get("parts", []):
        task_text += part.get("text", "")

    # For the green agent receiving A2A from bench-runner:
    # the green agent IS the assessor — it runs the task against the purple agent
    # and returns the score. But when the purple agent calls back to green's /mcp,
    # that's handled by the /mcp endpoint above.
    # Here we just acknowledge receipt.
    return {
        "jsonrpc": "2.0",
        "result": {
            "id": task_id,
            "status": {"state": "completed"},
            "artifacts": [
                {
                    "parts": [
                        {
                            "text": f"Task {task_id} received. Use /mcp endpoint to access tools. Session: {session_id}"
                        }
                    ]
                }
            ],
        },
    }
