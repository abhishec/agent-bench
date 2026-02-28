"""
FailureTracker — SQLite-backed per-run recorder + UCB1 bandit advisor.
Records every benchmark run, computes UCB1 scores, extracts training examples.
"""
from __future__ import annotations
import math
import sqlite3
import time
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# All task IDs (15 original + 8 new tau-bench)
ALL_TASK_IDS = [f"task_{i:02d}" for i in range(1, 24)]
PASS_THRESHOLD = 70.0

WEIGHTS = {
    "functional": 0.30,
    "policy_compliance": 0.20,
    "escalation": 0.15,
    "sequence": 0.15,
    "arithmetic": 0.10,
    "hallucination": 0.05,
    "communication": 0.05,
}

DB_PATH = Path(__file__).parent.parent / "data" / "failure_tracker.db"


class FailureTracker:
    def __init__(self, db_path: Path | None = None):
        self._db_path = db_path or DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    session_id TEXT,
                    timestamp REAL NOT NULL,
                    overall_score REAL,
                    functional REAL,
                    policy_compliance REAL,
                    escalation REAL,
                    sequence REAL,
                    arithmetic REAL,
                    hallucination REAL,
                    communication REAL,
                    answer_text TEXT,
                    error TEXT
                );

                CREATE TABLE IF NOT EXISTS tool_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL REFERENCES runs(id),
                    tool_name TEXT NOT NULL,
                    order_idx INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS failure_patterns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    dimension TEXT NOT NULL,
                    pattern_type TEXT NOT NULL,
                    count INTEGER DEFAULT 1,
                    last_seen REAL,
                    UNIQUE(task_id, dimension, pattern_type)
                );
            """)

    def record_run(
        self,
        task_id: str,
        score_result: Any,
        tool_calls: list[dict],
        session_id: str | None = None,
        answer: str = "",
        error: str | None = None,
    ) -> None:
        """Record one benchmark run."""
        dims = getattr(score_result, "dimensions", {}) or {}
        overall = getattr(score_result, "overall", 0.0) or 0.0

        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO runs
                  (task_id, session_id, timestamp, overall_score,
                   functional, policy_compliance, escalation, sequence,
                   arithmetic, hallucination, communication, answer_text, error)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    task_id, session_id, time.time(), overall,
                    dims.get("functional"), dims.get("policy_compliance"),
                    dims.get("escalation"), dims.get("sequence"),
                    dims.get("arithmetic"), dims.get("hallucination"),
                    dims.get("communication"),
                    answer[:500] if answer else None, error,
                ),
            )
            run_id = cur.lastrowid

            # Insert tool calls
            for idx, tc in enumerate(tool_calls):
                tool_name = tc.get("tool") or tc.get("action") or "unknown"
                conn.execute(
                    "INSERT INTO tool_calls (run_id, tool_name, order_idx) VALUES (?,?,?)",
                    (run_id, tool_name, idx),
                )

            # Upsert failure patterns
            if overall < PASS_THRESHOLD:
                for dim, weight in WEIGHTS.items():
                    score = dims.get(dim, 0.0) or 0.0
                    if score < 50.0:
                        pattern = self._classify_pattern(dim, score, tool_calls)
                        conn.execute(
                            """
                            INSERT INTO failure_patterns (task_id, dimension, pattern_type, count, last_seen)
                            VALUES (?,?,?,1,?)
                            ON CONFLICT(task_id, dimension, pattern_type) DO UPDATE SET
                              count = count + 1, last_seen = excluded.last_seen
                            """,
                            (task_id, dim, pattern, time.time()),
                        )

    def _classify_pattern(self, dim: str, score: float, tool_calls: list[dict]) -> str:
        no_tools = len(tool_calls) == 0
        if no_tools:
            return "no_tool_calls"
        if dim == "functional":
            return "critical_functional_failure" if score < 20 else "partial_functional_failure"
        if dim == "policy_compliance":
            return "policy_violation"
        if dim == "escalation":
            return "never_calls_escalate_tool"
        if dim == "sequence":
            return "wrong_tool_order"
        if dim == "arithmetic":
            return "calculation_error"
        if dim == "hallucination":
            return "hallucinated_data"
        if dim == "communication":
            return "poor_communication"
        return "low_score"

    def get_failure_patterns(self, task_id: str | None = None, last_n_hours: float = 24) -> list[dict]:
        """Return aggregated failure patterns."""
        cutoff = time.time() - last_n_hours * 3600
        with self._conn() as conn:
            if task_id:
                rows = conn.execute(
                    "SELECT * FROM failure_patterns WHERE task_id=? AND last_seen>=? ORDER BY count DESC",
                    (task_id, cutoff),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM failure_patterns WHERE last_seen>=? ORDER BY count DESC",
                    (cutoff,),
                ).fetchall()
        return [dict(r) for r in rows]

    def get_dimension_analysis(self, last_n_hours: float = 24) -> list[dict]:
        """Per-task, per-dimension failure analysis with recommendations."""
        cutoff = time.time() - last_n_hours * 3600
        with self._conn() as conn:
            run_rows = conn.execute(
                "SELECT * FROM runs WHERE timestamp >= ? ORDER BY task_id, timestamp",
                (cutoff,),
            ).fetchall()

        # Group by task_id
        by_task: dict[str, list] = {}
        for row in run_rows:
            by_task.setdefault(row["task_id"], []).append(row)

        results = []
        for task_id, runs in by_task.items():
            n = len(runs)
            for dim in WEIGHTS:
                scores = [r[dim] or 0.0 for r in runs]
                fail_count = sum(1 for s in scores if s < PASS_THRESHOLD)
                if fail_count == 0:
                    continue
                fail_rate = fail_count / n
                avg_score = sum(scores) / n
                pattern, recommendation = self._dimension_pattern(dim, scores, runs)
                results.append({
                    "task_id": task_id,
                    "dimension": dim,
                    "fail_rate": round(fail_rate, 3),
                    "avg_score": round(avg_score, 2),
                    "pattern": pattern,
                    "count": fail_count,
                    "total_runs": n,
                    "recommendation": recommendation,
                })
        return results

    def _dimension_pattern(self, dim: str, scores: list, run_rows: list) -> tuple[str, str]:
        avg = sum(scores) / len(scores)
        if dim == "functional":
            if avg < 20:
                return ("critical_functional_failure", "Agent is missing core functional steps. Add training with complete tool-call sequences.")
            return ("partial_functional_failure", "Agent completes some steps but misses key requirements. Add targeted training examples.")
        if dim == "policy_compliance":
            return ("policy_violation", "Agent violates policy constraints. Add policy-aware training examples.")
        if dim == "escalation":
            return ("never_calls_escalate_tool", "Agent skips escalation when required. Add escalation trigger examples.")
        if dim == "sequence":
            return ("wrong_tool_order", "Agent calls tools in wrong order. Add examples with explicit dependency ordering (e.g., always verify before mutate).")
        if dim == "arithmetic":
            return ("calculation_error", "Agent makes arithmetic errors. Add step-by-step calculation trace examples.")
        if dim == "hallucination":
            return ("hallucinated_data", "Agent invents values not from tools. Reinforce grounding: use only tool-returned values.")
        if dim == "communication":
            return ("poor_communication", "Agent fails to confirm before irreversible actions. Add confirm_with_user examples.")
        return ("low_score", f"Low {dim} score. Review scenario rubric.")

    def get_ucb_scores(self) -> dict[str, float]:
        """UCB1 bandit scores per task. Higher = more valuable to test next."""
        C = 1.41
        with self._conn() as conn:
            all_rows = conn.execute("SELECT task_id, overall_score FROM runs").fetchall()

        total_runs = len(all_rows)
        if total_runs == 0:
            return {tid: 10.0 for tid in ALL_TASK_IDS}

        task_data: dict[str, list] = {tid: [] for tid in ALL_TASK_IDS}
        for row in all_rows:
            tid = row["task_id"]
            if tid in task_data:
                task_data[tid].append(row["overall_score"] or 0.0)

        scores: dict[str, float] = {}
        for tid in ALL_TASK_IDS:
            task_runs = task_data[tid]
            n_task = len(task_runs)
            if n_task == 0:
                scores[tid] = 10.0
            else:
                fail_count = sum(1 for s in task_runs if s < PASS_THRESHOLD)
                avg_fail_rate = fail_count / n_task
                exploration = C * math.sqrt(math.log(total_runs) / n_task)
                scores[tid] = round(avg_fail_rate + exploration, 4)
        return scores

    def get_training_examples(self, last_n_hours: float = 4) -> list[dict]:
        """Extract structured training examples from recent failures."""
        from src.scenarios import SCENARIO_REGISTRY
        cutoff = time.time() - last_n_hours * 3600
        with self._conn() as conn:
            failed_runs = conn.execute(
                "SELECT * FROM runs WHERE timestamp >= ? AND overall_score < ? ORDER BY timestamp DESC",
                (cutoff, PASS_THRESHOLD),
            ).fetchall()

        examples = []
        for run in failed_runs:
            run_id = run["id"]
            task_id = run["task_id"]
            with self._conn() as conn:
                tc_rows = conn.execute(
                    "SELECT * FROM tool_calls WHERE run_id=? ORDER BY order_idx",
                    (run_id,),
                ).fetchall()

            tool_sequence = [r["tool_name"] for r in tc_rows]
            scenario_cls = SCENARIO_REGISTRY.get(task_id)
            task_text = policy_doc = ""
            expected_tools: list[str] = []
            if scenario_cls:
                sc = scenario_cls()
                task_text = sc.task_text
                policy_doc = sc.policy_doc
                expected_tools = getattr(sc.meta, "tools_available", [])

            dims = {d: run[d] or 0.0 for d in WEIGHTS}
            failed_dims = [d for d, s in dims.items() if s < 50.0]
            no_tools = len(tool_sequence) == 0
            what_went_wrong = _describe_failure(failed_dims, no_tools, tool_sequence, expected_tools)
            training_signal = _build_training_signal(failed_dims, no_tools, dims)

            examples.append({
                "task_id": task_id,
                "session_id": run["session_id"],
                "timestamp": datetime.fromtimestamp(run["timestamp"], tz=timezone.utc).isoformat(),
                "task_text": task_text,
                "policy_doc": policy_doc,
                "what_went_wrong": what_went_wrong,
                "actual_tool_sequence": tool_sequence,
                "expected_tools_available": expected_tools,
                "score_breakdown": {k: round(v, 2) for k, v in dims.items()},
                "overall_score": round(run["overall_score"] or 0.0, 2),
                "training_signal": training_signal,
                "agent_answer_snippet": (run["answer_text"] or "")[:300],
                "error": run["error"],
            })
        return examples


def _describe_failure(failed_dims, no_tools, actual_tools, expected_tools) -> str:
    if no_tools:
        return "Agent returned a response without calling any tools. Most severe failure: agent must use tools to interact with data."
    parts = []
    if "functional" in failed_dims:
        missing = [t for t in expected_tools if t not in actual_tools]
        if missing:
            parts.append(f"Missing required tool calls: {', '.join(missing)}.")
        else:
            parts.append("Called tools but produced incorrect functional outcome.")
    if "policy_compliance" in failed_dims:
        parts.append("Policy constraints were violated.")
    if "escalation" in failed_dims:
        parts.append("Failed to escalate when required.")
    if "sequence" in failed_dims:
        parts.append(f"Tool calls in wrong order. Actual: {actual_tools}.")
    if "arithmetic" in failed_dims:
        parts.append("Arithmetic calculations were incorrect.")
    if "hallucination" in failed_dims:
        parts.append("Agent used values not returned by tool calls.")
    if "communication" in failed_dims:
        parts.append("Agent did not confirm before irreversible actions.")
    return " ".join(parts) if parts else "Low scores across multiple dimensions."


def _build_training_signal(failed_dims, no_tools, dims) -> dict:
    penalty_breakdown = {}
    for dim, weight in WEIGHTS.items():
        score = dims.get(dim, 100.0)
        if score < 50.0:
            penalty_breakdown[dim] = round((50.0 - score) * weight, 4)
    priority_dim = max(penalty_breakdown, key=lambda d: penalty_breakdown[d]) if penalty_breakdown else "functional"
    focus_areas = (["tool_invocation"] if no_tools else []) + list(failed_dims)
    return {
        "priority_dimension": priority_dim,
        "penalty_breakdown": penalty_breakdown,
        "focus_areas": focus_areas,
        "is_total_failure": no_tools,
        "severity": "critical" if (no_tools or dims.get("functional", 100.0) < 20) else "moderate",
    }
