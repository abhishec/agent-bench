"""
TrainingDataFactory — converts benchmark run data into BrainOS fine-tuning JSONL.
Generates positive examples from passing runs and corrected examples from failures.
"""
from __future__ import annotations
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

S3_BUCKET = os.getenv("REPORT_BUCKET", "nexusbrain-codebuild-source-848269696611")
S3_TRAINING_PREFIX = "agentbench-training-data"
PASS_THRESHOLD = 70.0

SCENARIO_NAMES = {
    f"task_{i:02d}": name for i, name in enumerate([
        "E-commerce Order Management", "Procurement Approval", "Employee Offboarding",
        "Insurance Claims", "Invoice Processing", "SLA Monitoring", "Travel Rebooking",
        "Compliance Audit", "Subscription Management", "Payment Dispute",
        "Accounting Reconciliation", "Product Catalog Update", "Accounts Receivable",
        "Incident Response", "Quarterly Business Review", "Retail Return",
        "Retail Order Cancel", "Retail Exchange", "Retail Order Modify",
        "Retail Address Update", "Airline Booking", "Airline Flight Change",
        "Airline Cancellation",
    ], start=1)
}

SYSTEM_PROMPT_TEMPLATE = """\
You are an autonomous business operations agent running in a benchmark evaluation.

CRITICAL RULES:
1. NEVER ask the user for more information. All data you need is accessible via the provided tools.
2. Start calling tools IMMEDIATELY. Do not ask clarifying questions.
3. If a task mentions specific IDs (e.g. BK-001, ORD-001, EMP-001), call the relevant tool directly.
4. Complete ALL required actions end-to-end before writing your final summary.

POLICY:
{policy_doc}

Execute the task fully using available tools. Provide a concise summary after completing all actions."""

WRONG_APPROACH_LABELS = {
    "no_tools": "The agent responded without calling any tools — it should have called tools immediately.",
    "policy_violation": "The agent violated a policy constraint — it should have checked policy before acting.",
    "escalation_missed": "The agent failed to escalate when required by policy.",
    "wrong_tools": "The agent called tools in the wrong order — check dependency requirements.",
    "arithmetic_error": "The agent made calculation errors — verify all arithmetic step by step.",
    "hallucinated_data": "The agent used data not returned by tools — only use values from tool results.",
    "communication_failure": "The agent did not confirm with the user before irreversible actions.",
    "incomplete": "The agent did not complete all required steps.",
}


class TrainingDataFactory:

    def generate_positive_example(
        self,
        task_id: str,
        task_text: str,
        policy_doc: str,
        fixture: dict,
        tool_calls: list[dict],
        answer: str,
        score: float,
        difficulty: str = "none",
    ) -> dict | None:
        """Generate a positive training example from a passing run."""
        if score < PASS_THRESHOLD:
            return None

        messages = self._build_trajectory_messages(
            task_text, policy_doc, tool_calls, answer, fixture
        )

        return {
            "messages": messages,
            "metadata": {
                "task_id": task_id,
                "scenario": SCENARIO_NAMES.get(task_id, task_id),
                "difficulty": difficulty,
                "source": "benchmark_pass",
                "overall_score": round(score, 2),
                "training_type": "positive",
                "tool_count": len(tool_calls),
                "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            },
        }

    def generate_negative_example(
        self,
        task_id: str,
        task_text: str,
        policy_doc: str,
        fixture: dict,
        tool_calls: list[dict],
        answer: str,
        score: float,
        failure_cause: str,
        score_breakdown: dict,
    ) -> dict:
        """Generate a corrective training example from a failing run."""
        # Show wrong trajectory + correction
        wrong_messages = self._build_trajectory_messages(
            task_text, policy_doc, tool_calls, answer, fixture
        )

        failure_label = WRONG_APPROACH_LABELS.get(failure_cause, "The agent did not complete the task correctly.")
        correction = self._generate_correction_text(
            task_id, task_text, policy_doc, tool_calls, score_breakdown
        )

        # Add correction turn
        wrong_messages.append({
            "role": "user",
            "content": (
                f"TRAINING CORRECTION: {failure_label}\n\n"
                f"Score breakdown: {json.dumps({k: round(v,1) for k, v in score_breakdown.items()})}\n\n"
                f"Correct approach:\n{correction}"
            ),
        })
        wrong_messages.append({
            "role": "assistant",
            "content": (
                f"Understood. The correct approach for {SCENARIO_NAMES.get(task_id, task_id)} is:\n\n"
                f"{correction}\n\n"
                "I will follow this pattern in future tasks."
            ),
        })

        return {
            "messages": wrong_messages,
            "metadata": {
                "task_id": task_id,
                "scenario": SCENARIO_NAMES.get(task_id, task_id),
                "difficulty": "none",
                "source": "benchmark_fail_corrected",
                "overall_score": round(score, 2),
                "training_type": "negative_corrected",
                "failure_cause": failure_cause,
                "tool_count": len(tool_calls),
                "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            },
        }

    def _build_trajectory_messages(
        self,
        task_text: str,
        policy_doc: str,
        tool_calls: list[dict],
        answer: str,
        fixture: dict,
    ) -> list[dict]:
        messages = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT_TEMPLATE.format(policy_doc=policy_doc),
            },
            {"role": "user", "content": task_text},
        ]

        # Group tool calls into assistant+tool_result pairs
        for i, tc in enumerate(tool_calls):
            tool_name = tc.get("tool") or tc.get("action") or "unknown"
            tool_input = tc.get("params") or tc.get("input") or {}
            tool_result = tc.get("result") or tc.get("output") or f"Tool {tool_name} executed successfully"
            tool_id = f"toolu_{i:03d}_{tool_name}"

            messages.append({
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": tool_id,
                        "name": tool_name,
                        "input": tool_input if isinstance(tool_input, dict) else {},
                    }
                ],
            })
            messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": str(tool_result)[:500],
                    }
                ],
            })

        # Final answer
        if answer:
            messages.append({"role": "assistant", "content": answer})

        return messages

    def _generate_correction_text(
        self,
        task_id: str,
        task_text: str,
        policy_doc: str,
        actual_tool_calls: list[dict],
        score_breakdown: dict,
    ) -> str:
        """Generate a textual correction explaining the right approach."""
        from src.scenarios import SCENARIO_REGISTRY
        scenario_cls = SCENARIO_REGISTRY.get(task_id)
        if not scenario_cls:
            return "Follow the policy document and use all available tools to complete the task end-to-end."

        sc = scenario_cls()
        expected_tools = getattr(sc.meta, "tools_available", [])
        actual_tools = [tc.get("tool") or tc.get("action") for tc in actual_tool_calls]
        missing = [t for t in expected_tools if t not in actual_tools]
        dep_graph = getattr(sc.meta, "dependency_graph", {})

        lines = [f"For the {SCENARIO_NAMES.get(task_id, task_id)} scenario:"]
        if missing:
            lines.append(f"1. Missing tool calls: {', '.join(missing)}")
        if dep_graph:
            lines.append("2. Required tool order (dependencies):")
            for tool, deps in dep_graph.items():
                lines.append(f"   - {tool} requires: {', '.join(deps)} first")
        if score_breakdown.get("policy_compliance", 100) < 50:
            lines.append("3. Policy violation detected — always check policy constraints before executing.")
        if score_breakdown.get("sequence", 100) < 50:
            lines.append("4. Tool calls were out of order — follow the dependency graph above.")
        if score_breakdown.get("functional", 100) < 50:
            lines.append("5. Required state changes were not made — verify all mutations succeeded.")
        lines.append(f"6. Always complete these tools: {', '.join(expected_tools)}")

        return "\n".join(lines)

    def export_to_jsonl(self, examples: list[dict], output_path: str) -> int:
        """Write training examples as JSONL. Returns count written."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with open(path, "w") as f:
            for ex in examples:
                if ex:
                    f.write(json.dumps(ex) + "\n")
                    count += 1
        return count

    def upload_to_s3(self, local_path: str, s3_key: str | None = None) -> str:
        """Upload JSONL file to S3. Returns s3:// URL."""
        import boto3
        if s3_key is None:
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            s3_key = f"{S3_TRAINING_PREFIX}/{ts}_training_data.jsonl"
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.upload_file(local_path, S3_BUCKET, s3_key)
        return f"s3://{S3_BUCKET}/{s3_key}"

    def generate_from_tracker(self, last_n_hours: float = 4) -> tuple[list[dict], list[dict]]:
        """Pull runs from FailureTracker and generate training examples.
        Returns (positives, negatives).
        """
        from src.failure_tracker import FailureTracker, PASS_THRESHOLD
        from src.scenarios import SCENARIO_REGISTRY

        tracker = FailureTracker()
        positives, negatives = [], []

        # Get all recent runs
        import sqlite3, time
        from pathlib import Path
        db_path = Path(__file__).parent.parent / "data" / "failure_tracker.db"
        if not db_path.exists():
            return [], []

        cutoff = time.time() - last_n_hours * 3600
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        runs = conn.execute(
            "SELECT * FROM runs WHERE timestamp >= ? ORDER BY task_id, timestamp",
            (cutoff,)
        ).fetchall()

        for run in runs:
            task_id = run["task_id"]
            tc_rows = conn.execute(
                "SELECT tool_name FROM tool_calls WHERE run_id=? ORDER BY order_idx",
                (run["id"],)
            ).fetchall()
            tool_calls = [{"tool": r["tool_name"]} for r in tc_rows]

            sc_cls = SCENARIO_REGISTRY.get(task_id)
            if not sc_cls:
                continue
            sc = sc_cls()
            score = run["overall_score"] or 0.0
            dims = {d: run[d] or 0.0 for d in
                    ["functional", "policy_compliance", "escalation", "sequence", "arithmetic", "hallucination", "communication"]}

            if score >= PASS_THRESHOLD:
                ex = self.generate_positive_example(
                    task_id=task_id,
                    task_text=sc.task_text,
                    policy_doc=sc.policy_doc,
                    fixture={},
                    tool_calls=tool_calls,
                    answer=run["answer_text"] or "",
                    score=score,
                )
                if ex:
                    positives.append(ex)
            else:
                # Determine failure cause
                if not tool_calls:
                    cause = "no_tools"
                elif dims.get("functional", 100) < 50:
                    cause = "incomplete"
                elif dims.get("policy_compliance", 100) < 50:
                    cause = "policy_violation"
                elif dims.get("sequence", 100) < 50:
                    cause = "wrong_tools"
                else:
                    cause = "hallucinated_data"

                ex = self.generate_negative_example(
                    task_id=task_id,
                    task_text=sc.task_text,
                    policy_doc=sc.policy_doc,
                    fixture={},
                    tool_calls=tool_calls,
                    answer=run["answer_text"] or "",
                    score=score,
                    failure_cause=cause,
                    score_breakdown=dims,
                )
                negatives.append(ex)

        conn.close()
        return positives, negatives
