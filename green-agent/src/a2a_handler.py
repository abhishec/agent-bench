"""
AgentBeats-compatible A2A assessment request handler.

When the AgentBeats platform sends an assessment_request to our green agent,
this module parses it and runs the full benchmark suite against the participant.

EvalRequest format (AgentBeats standard):
{
  "participants": {"agent": "https://purple-agent-url"},
  "config": {
    "task_ids": ["task_01", "task_06"],  # optional, defaults to sample_tasks
    "difficulty": "none",                # optional
    "max_tasks": 10                      # optional, cap for quick runs
  }
}
"""
from __future__ import annotations
import asyncio
import json
import uuid
from typing import Any

from src.scenarios import SCENARIO_REGISTRY
from src.run_store import record_result
from src.failure_tracker import FailureTracker


# Default task sample for a standard AgentBeats assessment run
# Covers all 14 domains evenly (38 scenarios → pick 14 representative ones)
DEFAULT_SAMPLE_TASKS = [
    "task_01",  # e-commerce
    "task_02",  # procurement
    "task_04",  # insurance
    "task_06",  # operations  (known PASS)
    "task_08",  # compliance  (known PASS)
    "task_09",  # saas        (known PASS)
    "task_16",  # retail
    "task_21",  # airline
    "task_24",  # banking
    "task_26",  # HR
    "task_28",  # healthcare
    "task_30",  # supply chain
    "task_33",  # legal
    "task_35",  # IT helpdesk
]


async def run_agentbeats_assessment(
    message_text: str,
    own_url: str,
    session_prefix: str = "",
) -> dict[str, Any]:
    """
    Parse an AgentBeats EvalRequest and run the benchmark assessment.

    Returns a dict that becomes the A2A artifact payload.
    """
    from src.task_manager import run_assessment

    # ── Parse EvalRequest ────────────────────────────────────────────────────
    try:
        eval_req = json.loads(message_text)
    except json.JSONDecodeError:
        return {
            "error": "Invalid EvalRequest: could not parse JSON",
            "received": message_text[:200],
        }

    participants = eval_req.get("participants", {})
    config = eval_req.get("config", {})

    # Find the purple agent URL — participants may have any role key
    purple_url = None
    for role, url in participants.items():
        purple_url = str(url)
        break  # Take the first participant (business process: single agent)

    if not purple_url:
        return {
            "error": "No participants provided in EvalRequest",
            "eval_req": eval_req,
        }

    # Determine which tasks to run
    task_ids = config.get("task_ids", None)
    max_tasks = config.get("max_tasks", None)
    difficulty = config.get("difficulty", "none")
    run_all = config.get("run_all", False)

    if task_ids:
        # Validate they exist
        task_ids = [t for t in task_ids if t in SCENARIO_REGISTRY]
    elif run_all:
        task_ids = sorted(SCENARIO_REGISTRY.keys())
    else:
        task_ids = DEFAULT_SAMPLE_TASKS

    if max_tasks and max_tasks > 0:
        task_ids = task_ids[:max_tasks]

    print(f"[a2a_handler] Assessment: {len(task_ids)} tasks, difficulty={difficulty}, purple={purple_url}", flush=True)

    # ── Run each task ────────────────────────────────────────────────────────
    results = []
    passed = 0
    total_score = 0.0

    async def run_one(task_id: str) -> dict:
        session_id = f"{session_prefix}{task_id}_{uuid.uuid4().hex[:8]}"
        try:
            result = await run_assessment(
                task_id=task_id,
                purple_agent_url=purple_url,
                green_agent_url=own_url,
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
                "overall": scores.get("overall", 0),
                "passed": scores.get("overall", 0) >= 70.0,
                "scores": scores,
                "tool_calls": len(result.tool_calls),
                "error": result.error,
            }
        except Exception as e:
            print(f"[a2a_handler] task {task_id} failed: {e}", flush=True)
            return {
                "task_id": task_id,
                "overall": 0,
                "passed": False,
                "scores": {},
                "tool_calls": 0,
                "error": str(e),
            }

    # Run tasks concurrently (but cap concurrency to avoid overwhelming purple)
    semaphore = asyncio.Semaphore(3)

    async def run_with_sem(task_id):
        async with semaphore:
            return await run_one(task_id)

    task_results = await asyncio.gather(*[run_with_sem(t) for t in task_ids])

    for r in task_results:
        results.append(r)
        if r["passed"]:
            passed += 1
        total_score += r["overall"]

    avg_score = total_score / max(1, len(results))
    pass_rate = passed / max(1, len(results))

    # Domain breakdown
    domain_map = {
        "task_01": "e-commerce", "task_02": "procurement", "task_03": "hr",
        "task_04": "insurance", "task_05": "finance", "task_06": "operations",
        "task_07": "travel", "task_08": "compliance", "task_09": "saas",
        "task_10": "finance", "task_11": "accounting", "task_12": "e-commerce",
        "task_13": "accounting", "task_14": "operations", "task_15": "strategy",
        "task_16": "retail", "task_17": "retail", "task_18": "retail",
        "task_19": "retail", "task_20": "retail", "task_21": "airline",
        "task_22": "airline", "task_23": "airline", "task_24": "banking",
        "task_25": "banking", "task_26": "hr", "task_27": "hr",
        "task_28": "healthcare", "task_29": "healthcare", "task_30": "supply_chain",
        "task_31": "supply_chain", "task_32": "customer_success", "task_33": "legal",
        "task_34": "finance", "task_35": "it_helpdesk", "task_36": "marketing",
        "task_37": "real_estate", "task_38": "e-commerce",
    }
    domain_scores: dict[str, list[float]] = {}
    for r in results:
        domain = domain_map.get(r["task_id"], "other")
        domain_scores.setdefault(domain, []).append(r["overall"])
    domain_summary = {
        d: round(sum(s) / len(s), 1)
        for d, s in domain_scores.items()
    }

    return {
        "agent": "AgentBench Green — Business Process Improvement",
        "version": "2.0.0",
        "scenarios_total": len(SCENARIO_REGISTRY),
        "tasks_run": len(results),
        "tasks_passed": passed,
        "pass_rate": round(pass_rate, 3),
        "average_score": round(avg_score, 1),
        "difficulty": difficulty,
        "domain_scores": domain_summary,
        "results": results,
        "purple_agent": purple_url,
    }
