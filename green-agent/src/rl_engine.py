"""
AdaptiveEngine — wraps FailureTracker to provide curriculum learning recommendations.
"""
from __future__ import annotations
from typing import Any
from src.failure_tracker import FailureTracker, PASS_THRESHOLD, ALL_TASK_IDS


class AdaptiveEngine:
    def __init__(self):
        self._tracker = FailureTracker()

    @property
    def tracker(self) -> FailureTracker:
        return self._tracker

    def recommend_next_tasks(self, n: int = 5) -> list[str]:
        """Return top-n task IDs to test next (highest UCB1 score)."""
        scores = self._tracker.get_ucb_scores()
        return sorted(scores, key=lambda t: scores[t], reverse=True)[:n]

    def compute_reward(self, score: float, tool_count: int) -> float:
        """Reward in [0, 1]: 1.0 for pass, 0.0 for no tools, normalized otherwise."""
        if tool_count == 0:
            return 0.0
        if score >= PASS_THRESHOLD:
            return 1.0
        return round(score / 100.0, 4)

    def analyze_failure_cause(self, score_result: Any, tool_calls: list[dict]) -> str:
        """Return primary failure cause string."""
        if not tool_calls:
            return "no_tools"
        dims = getattr(score_result, "dimensions", {}) or {}
        overall = getattr(score_result, "overall", 0.0) or 0.0
        if overall >= PASS_THRESHOLD:
            return "pass"
        causes = {
            "policy_violation": dims.get("policy_compliance", 100.0) < 50.0,
            "escalation_missed": dims.get("escalation", 100.0) < 50.0,
            "wrong_tools": dims.get("sequence", 100.0) < 50.0,
            "arithmetic_error": dims.get("arithmetic", 100.0) < 50.0,
            "hallucinated_data": dims.get("hallucination", 100.0) < 50.0,
            "communication_failure": dims.get("communication", 100.0) < 50.0,
            "incomplete": dims.get("functional", 100.0) < 50.0,
        }
        for cause, triggered in causes.items():
            if triggered:
                return cause
        return "general_underperformance"

    def get_improvement_suggestions(self, task_id: str | None = None, last_n_hours: float = 24) -> list[dict]:
        """Return actionable improvement suggestions per failure pattern."""
        patterns = self._tracker.get_failure_patterns(task_id=task_id, last_n_hours=last_n_hours)
        suggestions = []
        for p in patterns:
            rec = _pattern_recommendation(p["pattern"])
            suggestions.append({
                "task_id": p["task_id"],
                "dimension": p["dimension"],
                "pattern": p["pattern"],
                "failure_count": p["count"],
                "recommendation": rec,
                "training_type": _training_type(p["pattern"]),
            })
        return suggestions


def _pattern_recommendation(pattern: str) -> str:
    return {
        "no_tool_calls": "Add few-shot examples demonstrating immediate tool invocation on task start.",
        "critical_functional_failure": "Add complete end-to-end tool-call sequences showing correct state mutations.",
        "partial_functional_failure": "Add targeted training examples for the specific missed steps.",
        "policy_violation": "Add policy-aware training with explicit constraint examples.",
        "never_calls_escalate_tool": "Add examples showing correct escalation trigger conditions.",
        "wrong_tool_order": "Add examples with explicit dependency ordering (e.g., always verify before mutate).",
        "calculation_error": "Add step-by-step calculation trace examples for discounts/fees/balances.",
        "hallucinated_data": "Reinforce grounding: agent must only use values returned from tool calls.",
        "poor_communication": "Add examples demonstrating confirm_with_user before irreversible actions.",
    }.get(pattern, f"Investigate {pattern} pattern and add relevant training examples.")


def _training_type(pattern: str) -> str:
    return {
        "no_tool_calls": "few-shot tool invocation",
        "critical_functional_failure": "end-to-end task completion",
        "partial_functional_failure": "targeted step completion",
        "policy_violation": "policy constraint examples",
        "never_calls_escalate_tool": "escalation trigger examples",
        "wrong_tool_order": "sequence ordering examples",
        "calculation_error": "arithmetic trace examples",
        "hallucinated_data": "grounding examples",
        "poor_communication": "confirmation examples",
    }.get(pattern, "general training")
