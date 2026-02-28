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
from src.run_store import record_result
from src.failure_tracker import FailureTracker
from src.rl_engine import AdaptiveEngine
from src.report_scheduler import ReportScheduler


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

_scheduler = ReportScheduler()


@app.on_event("startup")
async def _startup():
    await _scheduler.start()


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
    return {"status": "ok", "agent": "green-benchmark", "scenarios": len(SCENARIO_REGISTRY)}


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

    # Record run in in-memory store (for 4-hour reporter)
    try:
        record_result(
            task_id=req.task_id,
            scores=scores,
            tool_calls=result.tool_calls,
            answer=result.answer or "",
            error=result.error,
        )
    except Exception as _rs_err:
        print(f"[run_store] record_result failed: {_rs_err}", flush=True)

    # Record run in SQLite failure tracker (for UCB1 + training examples)
    try:
        FailureTracker().record_run(
            task_id=req.task_id,
            score_result=result.score,
            tool_calls=result.tool_calls,
            session_id=session_id,
            answer=result.answer or "",
            error=result.error,
        )
    except Exception as _ft_err:
        print(f"[FailureTracker] record_run failed (server): {_ft_err}", flush=True)

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


# ── RL / Adaptive Engine Endpoints ──────────────────────────────────────────
@app.get("/rl/status")
async def rl_status():
    """Return UCB1 bandit scores and top recommended tasks."""
    ae = AdaptiveEngine()
    ucb_scores = ae.tracker.get_ucb_scores()
    recommendations = ae.recommend_next_tasks(n=5)
    return {
        "ucb_scores": ucb_scores,
        "recommended_next_tasks": recommendations,
        "total_tasks": len(ucb_scores),
    }


@app.get("/rl/failures")
async def rl_failures(task_id: str | None = None, hours: float = 24):
    """Return failure patterns and dimension analysis."""
    ae = AdaptiveEngine()
    patterns = ae.tracker.get_failure_patterns(task_id=task_id, last_n_hours=hours)
    dimension_analysis = ae.tracker.get_dimension_analysis(last_n_hours=hours)
    suggestions = ae.get_improvement_suggestions(task_id=task_id, last_n_hours=hours)
    return {
        "failure_patterns": patterns,
        "dimension_analysis": dimension_analysis,
        "improvement_suggestions": suggestions,
    }


@app.get("/rl/training-data")
async def rl_training_data(hours: float = 4):
    """Return structured training examples from recent failures."""
    ft = FailureTracker()
    examples = ft.get_training_examples(last_n_hours=hours)
    return {
        "hours": hours,
        "count": len(examples),
        "examples": examples,
    }


# ── Report Endpoints ──────────────────────────────────────────────────────────
@app.post("/report/now")
async def report_now(hours: float = 4):
    """Generate and save a report immediately from the last N hours of runs."""
    from src.run_store import get_recent_runs
    from src.reporter import BenchmarkReporter
    import datetime

    runs = get_recent_runs(hours=hours)
    reporter = BenchmarkReporter()
    report = reporter.generate_report(runs)

    s3_url = None
    md_url = None
    try:
        s3_url = reporter.save_to_s3(report)
    except Exception as e:
        s3_url = f"(S3 save failed: {e})"
    try:
        md_url = reporter.save_markdown_report(report)
    except Exception as e:
        md_url = f"(markdown save failed: {e})"

    return {
        "generated_at": report["generated_at"],
        "period_hours": hours,
        "total_runs": report["total_runs"],
        "pass_rate": report["pass_rate"],
        "s3_json_url": s3_url,
        "s3_md_url": md_url,
        "report": report,
    }


@app.get("/report/latest")
async def report_latest():
    """Generate an in-memory report from the last 4 hours (no S3 save)."""
    from src.run_store import get_recent_runs
    from src.reporter import BenchmarkReporter

    runs = get_recent_runs(hours=4)
    reporter = BenchmarkReporter()
    report = reporter.generate_report(runs)
    return report


@app.get("/report/list")
async def report_list():
    """List saved reports in S3."""
    try:
        import boto3
        from src.reporter import BenchmarkReporter
        reporter = BenchmarkReporter()
        s3 = boto3.client("s3", region_name="us-east-1")
        resp = s3.list_objects_v2(
            Bucket=reporter.S3_BUCKET,
            Prefix=reporter.S3_PREFIX + "/",
        )
        objects = resp.get("Contents", [])
        reports = [
            {
                "key": obj["Key"],
                "size": obj["Size"],
                "last_modified": obj["LastModified"].isoformat(),
                "s3_url": f"s3://{reporter.S3_BUCKET}/{obj['Key']}",
            }
            for obj in sorted(objects, key=lambda x: x["LastModified"], reverse=True)
        ]
        return {"count": len(reports), "reports": reports}
    except Exception as e:
        return {"error": str(e), "reports": []}
