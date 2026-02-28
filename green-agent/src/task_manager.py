"""
Task Manager — orchestrates benchmark assessment runs.
Sends A2A tasks to the purple agent and scores the result.
"""
from __future__ import annotations
import asyncio
import os
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from src.mcp_server import seed_session_db, get_tool_calls, get_constraint_violations
from src.scorer import ScoreResult, score_task
from src.scenarios import SCENARIO_REGISTRY
from src.failure_tracker import FailureTracker


@dataclass
class AssessmentResult:
    task_id: str
    session_id: str
    answer: str
    score: ScoreResult
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


async def run_assessment(
    task_id: str,
    purple_agent_url: str,
    green_agent_url: str | None = None,
    difficulty: str = "none",
    session_id: str | None = None,
) -> AssessmentResult:
    """Run a full assessment: seed DB → send A2A → score result."""
    if session_id is None:
        session_id = str(uuid.uuid4())

    ScenarioClass = SCENARIO_REGISTRY.get(task_id)
    if not ScenarioClass:
        return AssessmentResult(
            task_id=task_id,
            session_id=session_id,
            answer="",
            score=ScoreResult(task_id=task_id),
            error=f"Unknown task_id: {task_id}",
        )

    scenario = ScenarioClass()
    fixture = scenario.load_fixture()
    await seed_session_db(session_id, fixture, task_id, difficulty=difficulty)

    # Build A2A request
    tools_endpoint = green_agent_url or os.getenv("GREEN_AGENT_MCP_URL", "http://localhost:9009")
    a2a_payload = {
        "jsonrpc": "2.0",
        "method": "tasks/send",
        "params": {
            "id": session_id,
            "message": {
                "role": "user",
                "parts": [{"text": scenario.task_text}],
            },
            "metadata": {
                "policy_doc": scenario.policy_doc,
                "tools_endpoint": tools_endpoint,
                "session_id": session_id,
                "difficulty": difficulty,
            },
        },
    }

    answer = ""
    error = None
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(purple_agent_url, json=a2a_payload)
            resp.raise_for_status()
            data = resp.json()
            result = data.get("result", {})
            artifacts = result.get("artifacts", [])
            if artifacts:
                parts = artifacts[0].get("parts", [])
                if parts:
                    answer = parts[0].get("text", "")
    except Exception as e:
        error = str(e)

    tool_calls = await get_tool_calls(session_id)
    violations = get_constraint_violations(session_id)
    score = score_task(task_id, fixture, fixture, tool_calls, answer, violations, difficulty=difficulty)

    try:
        FailureTracker().record_run(
            task_id=task_id,
            score_result=score,
            tool_calls=tool_calls,
            session_id=session_id,
            answer=answer or "",
            error=error,
        )
    except Exception as _ft_err:
        print(f"[FailureTracker] record_run failed: {_ft_err}", flush=True)

    return AssessmentResult(
        task_id=task_id,
        session_id=session_id,
        answer=answer,
        score=score,
        tool_calls=tool_calls,
        error=error,
    )
