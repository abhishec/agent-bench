"""
BenchmarkReporter — generates structured 4-hour reports and saves to S3.
"""
from __future__ import annotations
import datetime
import json
import os
from typing import Any

SCENARIO_NAMES = {
    "task_01": "E-commerce Order Management",
    "task_02": "Procurement Approval",
    "task_03": "Employee Offboarding",
    "task_04": "Insurance Claims",
    "task_05": "Invoice Processing",
    "task_06": "SLA Monitoring",
    "task_07": "Travel Rebooking",
    "task_08": "Compliance Audit",
    "task_09": "Subscription Management",
    "task_10": "Payment Dispute",
    "task_11": "Accounting Reconciliation",
    "task_12": "Product Catalog Update",
    "task_13": "Accounts Receivable",
    "task_14": "Incident Response",
    "task_15": "Quarterly Business Review",
    "task_16": "Retail Return",
    "task_17": "Retail Order Cancel",
    "task_18": "Retail Exchange",
    "task_19": "Retail Order Modify",
    "task_20": "Retail Address Update",
    "task_21": "Airline Booking",
    "task_22": "Airline Flight Change",
    "task_23": "Airline Cancellation",
}

SCORE_DIMENSIONS = ["functional", "policy_compliance", "escalation", "sequence", "arithmetic", "hallucination", "communication"]

TRAINING_RECS = {
    "no_tool_calls": "Purple needs few-shot examples of immediate tool invocation for this scenario",
    "low_functional": "Purple needs more training examples demonstrating correct state mutations for this scenario",
    "policy_violation": "Purple needs policy-aware training with explicit constraint examples",
    "sequence_error": "Purple needs examples of correct tool call ordering and dependency resolution",
    "general_underperformance": "Purple needs additional training examples across all dimensions for this scenario",
}

SKILL_GAPS = {
    "no_tool_calls": "Tool invocation on task start — agent must call tools immediately rather than asking clarifying questions",
    "low_functional": "Correct state mutations — agent must produce the right final DB state",
    "policy_violation": "Policy compliance — agent violates business constraints",
    "sequence_error": "Tool call ordering — agent calls tools out of required sequence",
    "general_underperformance": "General task competence — agent underperforms across multiple dimensions",
}


class BenchmarkReporter:
    S3_BUCKET = os.getenv("REPORT_BUCKET", "nexusbrain-codebuild-source-848269696611")
    S3_PREFIX = "agentbench-reports"

    def generate_report(self, runs_data: list[dict]) -> dict:
        now = datetime.datetime.utcnow().isoformat() + "Z"
        total = len(runs_data)
        pass_count = sum(1 for r in runs_data if r.get("passed", False))
        pass_rate = pass_count / total if total > 0 else 0.0

        # Dimension analysis
        dimension_analysis = {}
        for dim in SCORE_DIMENSIONS:
            scores = [r.get("scores", {}).get(dim, 0.0) or 0.0 for r in runs_data]
            if not scores:
                dimension_analysis[dim] = {"avg_score": 0.0, "fail_rate": 0.0, "trend": "stable"}
                continue
            fail_rate = sum(1 for s in scores if s < 70.0) / len(scores)
            avg_score = sum(scores) / len(scores)
            # Trend: compare first half vs second half
            mid = len(scores) // 2
            if mid > 0 and len(scores) > mid:
                first_half = sum(scores[:mid]) / mid
                second_half = sum(scores[mid:]) / (len(scores) - mid)
                if second_half - first_half > 5:
                    trend = "improving"
                elif first_half - second_half > 5:
                    trend = "declining"
                else:
                    trend = "stable"
            else:
                trend = "stable"
            dimension_analysis[dim] = {
                "avg_score": round(avg_score, 2),
                "fail_rate": round(fail_rate, 3),
                "trend": trend,
            }

        # Per-task analysis
        task_runs: dict[str, list] = {}
        for r in runs_data:
            tid = r.get("task_id", "unknown")
            task_runs.setdefault(tid, []).append(r)

        top_failures = []
        for tid, runs in task_runs.items():
            failing = [r for r in runs if not r.get("passed", False)]
            fail_rate = len(failing) / len(runs)
            if fail_rate == 0:
                continue
            avg_score = sum(r.get("scores", {}).get("overall", 0.0) or 0.0 for r in runs) / len(runs)
            # Primary cause
            avg_tools = sum(r.get("tool_calls_count", 0) for r in failing) / len(failing)
            avg_functional = sum(r.get("scores", {}).get("functional", 0.0) or 0.0 for r in failing) / len(failing)
            avg_policy = sum(r.get("scores", {}).get("policy_compliance", 0.0) or 0.0 for r in failing) / len(failing)
            avg_seq = sum(r.get("scores", {}).get("sequence", 0.0) or 0.0 for r in failing) / len(failing)
            if avg_tools == 0:
                cause = "no_tool_calls"
            elif avg_functional < 50:
                cause = "low_functional"
            elif avg_policy < 50:
                cause = "policy_violation"
            elif avg_seq < 50:
                cause = "sequence_error"
            else:
                cause = "general_underperformance"

            # Worst run example
            worst = min(runs, key=lambda r: r.get("scores", {}).get("overall", 100.0) or 100.0)
            top_failures.append({
                "task_id": tid,
                "scenario": SCENARIO_NAMES.get(tid, tid),
                "fail_rate": round(fail_rate, 3),
                "avg_score": round(avg_score, 1),
                "primary_cause": cause,
                "missing_tools": [],
                "policy_violations": [],
                "example_bad_answer": worst.get("answer_snippet", ""),
                "training_recommendation": TRAINING_RECS.get(cause, "Review scenario"),
            })
        top_failures.sort(key=lambda x: x["fail_rate"], reverse=True)

        # Training signals grouped by cause
        cause_groups: dict[str, list] = {}
        for tf in top_failures:
            cause_groups.setdefault(tf["primary_cause"], []).append(tf)

        brainos_training_signals = []
        for cause, tfs in cause_groups.items():
            avg_fr = sum(t["fail_rate"] for t in tfs) / len(tfs)
            brainos_training_signals.append({
                "priority": "HIGH" if avg_fr > 0.6 else "MEDIUM",
                "skill_gap": SKILL_GAPS.get(cause, cause),
                "affected_tasks": [t["task_id"] for t in tfs],
                "training_type": {
                    "no_tool_calls": "few-shot examples",
                    "low_functional": "state mutation examples",
                    "policy_violation": "policy constraint examples",
                    "sequence_error": "sequence ordering examples",
                    "general_underperformance": "mixed training examples",
                }.get(cause, "training examples"),
                "suggested_examples": [
                    {"input": SCENARIO_NAMES.get(t["task_id"], t["task_id"]),
                     "correct_action": f"Complete {t['scenario']} task using available tools"}
                    for t in tfs
                ],
            })

        # Difficulty ranking
        difficulty_ranking = []
        for tid, runs in task_runs.items():
            avg_score = sum(r.get("scores", {}).get("overall", 0.0) or 0.0 for r in runs) / len(runs)
            if avg_score < 50:
                difficulty = "VERY HARD"
            elif avg_score < 70:
                difficulty = "HARD"
            elif avg_score < 85:
                difficulty = "MEDIUM"
            else:
                difficulty = "EASY"
            if avg_score < 40:
                action = "rubric adjustment"
            elif avg_score < 60:
                action = "more training examples"
            elif avg_score < 75:
                action = "minor tuning"
            else:
                action = "maintain"
            difficulty_ranking.append({
                "task_id": tid,
                "scenario": SCENARIO_NAMES.get(tid, tid),
                "difficulty": difficulty,
                "avg_score": round(avg_score, 1),
                "recommended_action": action,
            })
        difficulty_ranking.sort(key=lambda x: x["avg_score"])

        # Next 4h recommendations (5 lowest)
        next_tests = [x["task_id"] for x in difficulty_ranking[:5]]

        return {
            "generated_at": now,
            "period_hours": 4,
            "total_runs": total,
            "pass_rate": round(pass_rate, 3),
            "dimension_analysis": dimension_analysis,
            "top_failures": top_failures,
            "brainos_training_signals": brainos_training_signals,
            "scenario_difficulty_ranking": difficulty_ranking,
            "next_4h_recommended_tests": next_tests,
        }

    def save_to_s3(self, report: dict) -> str:
        import boto3
        ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        key = f"{self.S3_PREFIX}/{ts}_report.json"
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.put_object(
            Bucket=self.S3_BUCKET,
            Key=key,
            Body=json.dumps(report, indent=2),
            ContentType="application/json",
        )
        return f"s3://{self.S3_BUCKET}/{key}"

    def save_markdown_report(self, report: dict) -> str:
        import boto3
        lines = [
            "# AgentBench 4-Hour Report",
            "",
            f"Generated: {report['generated_at']} | Period: {report['period_hours']}h | "
            f"Runs: {report['total_runs']} | Pass Rate: {report['pass_rate']:.1%}",
            "",
            "## Dimension Performance",
            "",
            "| Dimension | Avg Score | Fail Rate | Trend |",
            "|-----------|-----------|-----------|-------|",
        ]
        for dim, data in report.get("dimension_analysis", {}).items():
            lines.append(f"| {dim} | {data['avg_score']:.1f} | {data['fail_rate']:.1%} | {data['trend']} |")
        lines.extend(["", "## Top Failures", ""])
        for tf in report.get("top_failures", []):
            lines.extend([
                f"### {tf['scenario']} ({tf['task_id']})",
                f"- **Fail Rate**: {tf['fail_rate']:.1%}",
                f"- **Cause**: {tf['primary_cause']}",
                f"- **Recommendation**: {tf['training_recommendation']}",
                f"- **Example**: {tf.get('example_bad_answer', '')[:100]}",
                "",
            ])
        lines.extend(["## Training Signals", ""])
        for sig in report.get("brainos_training_signals", []):
            lines.extend([
                f"### [{sig['priority']}] {sig['skill_gap']}",
                f"- **Training Type**: {sig['training_type']}",
                f"- **Affected Tasks**: {', '.join(sig['affected_tasks'])}",
                "- **Suggested Examples**:",
            ])
            for ex in sig.get("suggested_examples", []):
                lines.append(f"  - {ex['input']}: {ex['correct_action']}")
            lines.append("")
        lines.extend(["## Difficulty Ranking", "", "| Task | Scenario | Difficulty | Avg Score | Action |", "|------|----------|------------|-----------|--------|"])
        for dr in report.get("scenario_difficulty_ranking", []):
            lines.append(f"| {dr['task_id']} | {dr['scenario']} | {dr['difficulty']} | {dr['avg_score']} | {dr['recommended_action']} |")
        lines.extend(["", "## Recommended Tests (Next 4h)", ""])
        for t in report.get("next_4h_recommended_tests", []):
            lines.append(f"- {t}: {SCENARIO_NAMES.get(t, t)}")

        md_content = "\n".join(lines)
        ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        key = f"{self.S3_PREFIX}/{ts}_report.md"
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.put_object(Bucket=self.S3_BUCKET, Key=key, Body=md_content, ContentType="text/markdown")
        return f"s3://{self.S3_BUCKET}/{key}"
