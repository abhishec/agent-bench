"""
Green-Agent Scorer — 7-dimension evaluation engine.

Dimensions and weights:
  functional        30%
  policy_compliance 20%
  escalation        15%
  sequence          15%
  arithmetic        10%
  hallucination      5%
  communication      5%
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

WEIGHTS = {
    "functional": 0.30,
    "policy_compliance": 0.20,
    "escalation": 0.15,
    "sequence": 0.15,
    "arithmetic": 0.10,
    "hallucination": 0.05,
    "communication": 0.05,
}

DIFFICULTY_MULTIPLIERS = {
    "none": 1.0,
    "easy": 1.1,
    "medium": 1.2,
    "hard": 1.4,
    "adversarial": 1.6,
}


@dataclass
class ScoreResult:
    task_id: str
    dimensions: dict[str, float] = field(default_factory=dict)
    constraint_violations: list[str] = field(default_factory=list)
    difficulty: str = "none"

    @property
    def overall(self) -> float:
        raw = sum(
            self.dimensions.get(dim, 0.0) * weight
            for dim, weight in WEIGHTS.items()
        )
        multiplier = DIFFICULTY_MULTIPLIERS.get(self.difficulty, 1.0)
        return min(100.0, raw * multiplier)

    def summary(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "overall": round(self.overall, 2),
            "dimensions": {k: round(v, 2) for k, v in self.dimensions.items()},
            "constraint_violations": self.constraint_violations,
            "difficulty": self.difficulty,
            "difficulty_multiplier": DIFFICULTY_MULTIPLIERS.get(self.difficulty, 1.0),
            "passed": self.overall >= 70.0,
        }


def score_task(
    task_id: str,
    initial_db: dict[str, Any],
    final_db: dict[str, Any],
    actions_log: list[dict[str, Any]],
    agent_output: str,
    constraint_violations: list[str] | None = None,
    difficulty: str = "none",
) -> ScoreResult:
    """
    Score a completed task using its registered scenario.

    Args:
        task_id: e.g. 'task_01'
        initial_db: DB state before the task (from fixture seed)
        final_db: DB state after agent finished
        actions_log: ordered list of tool calls made by the agent
        agent_output: final text response from the agent
        constraint_violations: list of ToolError('CONSTRAINT_VIOLATION') violations recorded by mcp_server

    Returns:
        ScoreResult with all 7 dimensions populated
    """
    from .scenarios import SCENARIO_REGISTRY

    scenario_cls = SCENARIO_REGISTRY.get(task_id)
    if scenario_cls is None:
        raise ValueError(f"No scenario registered for task_id={task_id!r}")

    scenario = scenario_cls()
    dimensions = scenario.score(initial_db, final_db, actions_log, agent_output)

    # Enforce constraint violation penalty: each ToolError('CONSTRAINT_VIOLATION') docks
    # 25 points from policy_compliance and 15 from functional
    violations = constraint_violations or []
    if violations:
        dimensions["policy_compliance"] = max(
            0.0, dimensions.get("policy_compliance", 100.0) - 25.0 * len(violations)
        )
        dimensions["functional"] = max(
            0.0, dimensions.get("functional", 100.0) - 15.0 * len(violations)
        )

    # Clamp all dimensions to [0, 100]
    for dim in WEIGHTS:
        dimensions[dim] = max(0.0, min(100.0, dimensions.get(dim, 0.0)))

    return ScoreResult(
        task_id=task_id,
        dimensions=dimensions,
        constraint_violations=violations,
        difficulty=difficulty,
    )


def lcs_length(a: list, b: list) -> int:
    """Longest common subsequence length for sequence scoring."""
    m, n = len(a), len(b)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if a[i - 1] == b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    return dp[m][n]


def sequence_score(actual_order: list[str], expected_order: list[str]) -> float:
    """
    Score the action sequence using LCS similarity.
    Returns 0.0–100.0.
    """
    if not expected_order:
        return 100.0
    if not actual_order:
        return 0.0
    lcs = lcs_length(actual_order, expected_order)
    return 100.0 * lcs / len(expected_order)
