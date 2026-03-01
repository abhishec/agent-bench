"""
Green Agent — FastAPI server.
Exposes: GET /.well-known/agent-card.json, POST / (A2A), POST /mcp, GET /mcp/tools, GET /health
         POST /benchmark  — run a benchmark task against purple and return scores
"""
from __future__ import annotations
import json
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
    "description": "Benchmark orchestrator that issues tasks to AI agents and scores their responses across 38 business scenarios spanning e-commerce, retail, airline, banking, HR, healthcare, supply chain, legal, finance, IT, marketing, and real estate.",
    "version": "2.0.0",
    "url": os.getenv("GREEN_AGENT_HOST_URL", "http://localhost:9009"),
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


# ── Scenario Listing ─────────────────────────────────────────────────────────
@app.get("/scenarios")
async def list_scenarios():
    """List all registered scenarios with metadata."""
    tasks = []
    for task_id, cls in sorted(SCENARIO_REGISTRY.items()):
        sc = cls()
        tasks.append({
            "task_id": task_id,
            "domain": _infer_domain(task_id),
            "tools_available": getattr(sc.meta, "tools_available", []),
            "tool_count": len(getattr(sc.meta, "tools_available", [])),
            "escalation_required": getattr(sc.meta, "escalation_required", False),
        })
    return {"total": len(tasks), "tasks": tasks}


def _infer_domain(task_id: str) -> str:
    domains = {
        "task_01": "e-commerce", "task_02": "procurement", "task_03": "hr",
        "task_04": "insurance", "task_05": "finance", "task_06": "operations",
        "task_07": "travel", "task_08": "compliance", "task_09": "saas",
        "task_10": "finance", "task_11": "accounting", "task_12": "e-commerce",
        "task_13": "accounting", "task_14": "operations", "task_15": "strategy",
        "task_16": "retail", "task_17": "retail", "task_18": "retail",
        "task_19": "retail", "task_20": "retail", "task_21": "airline",
        "task_22": "airline", "task_23": "airline",
    }
    return domains.get(task_id, "business")



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


class BatchBenchmarkRequest(BaseModel):
    task_ids: list
    purple_url: str = ""
    purple_agent_url: str = ""  # alias for backwards compat
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


# ── Batch Benchmark ──────────────────────────────────────────────────────────
@app.post("/benchmark/batch")
async def run_benchmark_batch(req: BatchBenchmarkRequest):
    """Run multiple benchmark tasks concurrently.
    Body: {task_ids: [...], purple_url: ..., difficulty: ...}
    """
    import asyncio
    from src.task_manager import run_assessment

    task_ids = req.task_ids
    # Accept either field name for backwards compat
    purple_url = (
        req.purple_url
        or req.purple_agent_url
        or os.getenv("PURPLE_AGENT_URL", "http://localhost:9010")
    )
    difficulty = req.difficulty
    host_url = _own_url()

    async def run_one(task_id):
        import uuid as _uuid
        session_id = str(_uuid.uuid4())
        try:
            result = await run_assessment(
                task_id=task_id,
                purple_agent_url=purple_url,
                green_agent_url=host_url,
                difficulty=difficulty,
                session_id=session_id,
            )
            scores = result.score.summary()
            # Record in stores
            try:
                record_result(
                    task_id=task_id,
                    scores=scores,
                    tool_calls=result.tool_calls,
                    answer=result.answer or "",
                    error=result.error,
                )
            except Exception:
                pass
            try:
                FailureTracker().record_run(
                    task_id=task_id,
                    score_result=result.score,
                    tool_calls=result.tool_calls,
                    session_id=session_id,
                    answer=result.answer or "",
                    error=result.error,
                )
            except Exception:
                pass
            return {
                "task_id": task_id,
                "scores": scores,
                "tool_calls_count": len(result.tool_calls),
                "answer_snippet": (result.answer or "")[:100],
                "error": result.error,
            }
        except Exception as e:
            return {"task_id": task_id, "error": str(e), "scores": {}}

    results = await asyncio.gather(*[run_one(t) for t in task_ids])
    passed = sum(
        1 for r in results
        if (r.get("scores", {}).get("overall", 0) or 0) >= 70.0
    )
    return {
        "total": len(results),
        "passed": passed,
        "pass_rate": round(passed / max(1, len(results)), 3),
        "results": list(results),
    }


# ── A2A Receiver (AgentBeats-compatible) ─────────────────────────────────────
# Supports both legacy "tasks/send" and A2A SDK "message/send" method names.
# When message text is a valid EvalRequest JSON, runs the full benchmark.
# Otherwise, falls back to MCP tool acknowledgement (purple-agent calls).
@app.post("/")
async def a2a_handler(request: Request):
    body = await request.json()

    method = body.get("method", "")
    # Support both A2A SDK method names
    if method not in ("tasks/send", "message/send"):
        raise HTTPException(400, f"Unsupported A2A method: {method}. Use tasks/send or message/send")

    params = body.get("params", {})
    # A2A SDK 0.3.x uses "message" key directly in params
    message = params.get("message", {})
    task_id = (
        params.get("id")
        or message.get("messageId")
        or str(uuid.uuid4())
    )
    context_id = message.get("contextId", task_id)
    metadata = params.get("metadata", {})
    session_id = metadata.get("session_id", task_id)

    # Extract text from message parts (handles both {"text": "..."} and {"kind":"text","text":"..."})
    task_text = ""
    for part in message.get("parts", []):
        task_text += part.get("text", "")

    # ── AgentBeats assessment_request detection ──────────────────────────────
    # If the message is a valid EvalRequest JSON, run the full benchmark
    is_eval_request = False
    try:
        parsed = json.loads(task_text)
        if "participants" in parsed:
            is_eval_request = True
    except (json.JSONDecodeError, TypeError):
        pass

    if is_eval_request:
        from src.a2a_handler import run_agentbeats_assessment
        print(f"[a2a] Received AgentBeats assessment_request, starting benchmark...", flush=True)
        result = await run_agentbeats_assessment(
            message_text=task_text,
            own_url=_own_url(),
            session_prefix=f"{task_id[:8]}_",
        )
        return {
            "jsonrpc": "2.0",
            "id": body.get("id"),
            "result": {
                "id": task_id,
                "contextId": context_id,
                "status": {"state": "completed"},
                "artifacts": [
                    {
                        "artifactId": str(uuid.uuid4()),
                        "name": "evaluation_result",
                        "parts": [
                            {
                                "kind": "text",
                                "text": json.dumps(result),
                            }
                        ],
                    }
                ],
            },
        }

    # ── Legacy: purple-agent tool call acknowledgement ───────────────────────
    return {
        "jsonrpc": "2.0",
        "id": body.get("id"),
        "result": {
            "id": task_id,
            "contextId": context_id,
            "status": {"state": "completed"},
            "artifacts": [
                {
                    "artifactId": str(uuid.uuid4()),
                    "name": "ack",
                    "parts": [
                        {
                            "kind": "text",
                            "text": f"Task {task_id} received. Use /mcp endpoint to access tools. Session: {session_id}"
                        }
                    ],
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


# -- Training Data Endpoints --------------------------------------------------
@app.post("/training-data/export")
async def export_training_data(hours: float = 4):
    """Export recent benchmark runs as BrainOS fine-tuning JSONL to S3."""
    from src.training_data_factory import TrainingDataFactory
    import tempfile, os

    factory = TrainingDataFactory()
    positives, negatives = factory.generate_from_tracker(last_n_hours=hours)

    all_examples = positives + negatives
    if not all_examples:
        return {"status": "no_data", "message": f"No runs found in last {hours} hours", "count": 0}

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        tmp_path = f.name
        for ex in all_examples:
            if ex:
                f.write(json.dumps(ex) + "\n")

    try:
        s3_url = factory.upload_to_s3(tmp_path)
        os.unlink(tmp_path)
        return {
            "status": "ok",
            "s3_url": s3_url,
            "total_examples": len(all_examples),
            "positive_examples": len(positives),
            "negative_examples": len(negatives),
            "hours_covered": hours,
        }
    except Exception as e:
        os.unlink(tmp_path)
        return {
            "status": "ok_local_only",
            "s3_error": str(e),
            "total_examples": len(all_examples),
            "positive_examples": len(positives),
            "negative_examples": len(negatives),
        }


@app.get("/training-data/stats")
async def training_data_stats():
    """Show stats about available training data."""
    from src.failure_tracker import FailureTracker
    tracker = FailureTracker()
    ucb = tracker.get_ucb_scores()
    examples_4h = tracker.get_training_examples(last_n_hours=4)
    examples_24h = tracker.get_training_examples(last_n_hours=24)

    severity_counts = {"critical": 0, "moderate": 0}
    for ex in examples_24h:
        sev = ex.get("training_signal", {}).get("severity", "moderate")
        severity_counts[sev] = severity_counts.get(sev, 0) + 1

    return {
        "training_examples_last_4h": len(examples_4h),
        "training_examples_last_24h": len(examples_24h),
        "severity_breakdown": severity_counts,
        "top_failing_tasks": sorted(ucb, key=lambda t: ucb[t], reverse=True)[:5],
        "tasks_needing_training": [t for t, s in ucb.items() if s > 2.0],
    }
