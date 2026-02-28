"""
In-memory run history store for the 4-hour reporter.
"""
from __future__ import annotations
import time
from datetime import datetime, timezone
from typing import Any

_run_history: list[dict] = []


def record_result(
    task_id: str,
    scores: dict,
    tool_calls: list[dict],
    answer: str = "",
    error: str | None = None,
) -> None:
    """Append a run record to in-memory history."""
    tool_names = [tc.get("tool") or tc.get("action") or "unknown" for tc in tool_calls]
    _run_history.append({
        "task_id": task_id,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "_ts": time.time(),
        "scores": scores,
        "tool_calls_count": len(tool_calls),
        "tool_names": tool_names,
        "answer_snippet": answer[:200],
        "passed": (scores.get("overall") or 0.0) >= 70.0,
        "error": error,
    })


def get_recent_runs(hours: float = 4) -> list[dict]:
    """Return runs from the last N hours (oldest first)."""
    cutoff = time.time() - hours * 3600
    return [r for r in _run_history if r["_ts"] >= cutoff]


def get_all_runs() -> list[dict]:
    """Return copy of full run history."""
    return list(_run_history)


def clear_runs() -> None:
    """Clear run history (for tests)."""
    _run_history.clear()
