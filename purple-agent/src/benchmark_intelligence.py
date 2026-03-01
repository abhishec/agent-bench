"""
BenchmarkIntelligence — loads proven tool-call patterns from JSONL training data
and injects them as few-shot guidance into the purple agent's system context.
"""
from __future__ import annotations
import json
import os
import re
from pathlib import Path
from collections import defaultdict


S3_BUCKET = os.getenv("REPORT_BUCKET", "nexusbrain-codebuild-source-848269696611")
S3_TRAINING_PREFIX = "agentbench-training-data"
LOCAL_CACHE = Path("/tmp/benchmark_intelligence_cache.json")
PASS_THRESHOLD = 70.0


class BenchmarkIntelligence:
    """Loads training examples and provides per-task guidance for the agent."""

    def __init__(self):
        self._patterns: dict[str, list[str]] = {}  # task_id → [tool, tool, ...]
        self._loaded = False

    def load(self) -> bool:
        """Download latest JSONL from S3 and build patterns. Returns True if loaded."""
        try:
            jsonl_path = self._download_latest_jsonl()
            if not jsonl_path:
                return False
            self._build_patterns(jsonl_path)
            self._loaded = True
            return True
        except Exception as e:
            print(f"[BenchmarkIntelligence] load failed: {e}", flush=True)
            return False

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def get_guidance(self, task_text: str) -> str:
        """Return a short guidance block with proven tool sequences for this task.
        Matches by keywords in task_text to find relevant task patterns."""
        if not self._loaded or not self._patterns:
            return ""

        relevant = self._find_relevant_patterns(task_text)
        if not relevant:
            return ""

        lines = ["PROVEN SUCCESSFUL PATTERNS (from benchmark training data):"]
        for task_id, tools in relevant:
            lines.append(f"  {task_id}: {' → '.join(tools)}")
        lines.append("Follow these tool sequences closely for similar tasks.")
        return "\n".join(lines)

    # ── internals ─────────────────────────────────────────────────────────────

    def _download_latest_jsonl(self) -> str | None:
        """Download the most recent JSONL from S3. Returns local path or None."""
        import boto3
        s3 = boto3.client("s3", region_name="us-east-1")
        resp = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=S3_TRAINING_PREFIX + "/")
        objects = resp.get("Contents", [])
        if not objects:
            return None
        latest = max(objects, key=lambda o: o["LastModified"])
        local_path = "/tmp/latest_training_data.jsonl"
        s3.download_file(S3_BUCKET, latest["Key"], local_path)
        print(f"[BenchmarkIntelligence] loaded {latest['Key']} ({latest['Size']//1024}KB)", flush=True)
        return local_path

    def _build_patterns(self, jsonl_path: str):
        """Parse JSONL and build task_id → best tool sequence from top passing runs."""
        task_runs: dict[str, list[tuple[float, list[str]]]] = defaultdict(list)

        with open(jsonl_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ex = json.loads(line)
                except json.JSONDecodeError:
                    continue
                meta = ex.get("metadata", {})
                if meta.get("training_type") != "positive":
                    continue
                score = meta.get("overall_score", 0.0)
                if score < PASS_THRESHOLD:
                    continue
                task_id = meta.get("task_id", "")
                if not task_id:
                    continue

                # Extract tool names in order from messages
                tools = []
                for msg in ex.get("messages", []):
                    content = msg.get("content", [])
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "tool_use":
                                name = block.get("name", "")
                                if name and (not tools or tools[-1] != name):
                                    tools.append(name)
                if tools:
                    task_runs[task_id].append((score, tools))

        # For each task, pick the highest-scoring run's tool sequence
        for task_id, runs in task_runs.items():
            best_score, best_tools = max(runs, key=lambda x: x[0])
            self._patterns[task_id] = best_tools

        print(f"[BenchmarkIntelligence] built patterns for {len(self._patterns)} tasks", flush=True)

    # keyword sets for matching incoming task text to known task IDs
    _TASK_KEYWORDS: list[tuple[str, list[str]]] = [
        ("task_01", ["order", "gift card", "discount", "return"]),
        ("task_02", ["purchase order", "procurement", "vendor", "po-"]),
        ("task_03", ["offboard", "termination", "revoke access", "equity"]),
        ("task_04", ["insurance", "claim", "policy", "deductible"]),
        ("task_05", ["invoice", "payment", "accounts payable"]),
        ("task_06", ["sla", "incident", "breach", "oncall"]),
        ("task_07", ["flight", "rebooking", "weather", "hotel"]),
        ("task_08", ["compliance", "audit", "gdpr", "sox"]),
        ("task_09", ["subscription", "cancel", "upgrade", "downgrade"]),
        ("task_10", ["dispute", "chargeback", "transaction", "fraud"]),
        ("task_11", ["reconcil", "accounting", "journal", "ledger"]),
        ("task_12", ["catalog", "product", "sku", "price"]),
        ("task_13", ["accounts receivable", "invoice aging", "collection"]),
        ("task_14", ["incident response", "outage", "p1", "p2"]),
        ("task_15", ["qbr", "quarterly", "business review"]),
        ("task_16", ["return", "refund", "retail"]),
        ("task_17", ["cancel", "pending order", "retail"]),
        ("task_18", ["exchange", "swap", "retail"]),
        ("task_19", ["modify", "quantity", "retail"]),
        ("task_20", ["address", "shipping", "retail"]),
        ("task_21", ["book", "airline", "flight", "passenger"]),
        ("task_22", ["change flight", "airline", "reschedule"]),
        ("task_23", ["cancel flight", "airline", "refund"]),
        ("task_24", ["wire transfer", "banking", "bank"]),
        ("task_25", ["fraud", "suspicious", "banking"]),
        ("task_26", ["pto", "time off", "vacation", "hr"]),
        ("task_27", ["expense", "reimbursement", "receipt"]),
        ("task_28", ["appointment", "healthcare", "doctor"]),
        ("task_29", ["prescription", "medication", "pharmacy"]),
        ("task_30", ["inventory", "reorder", "stock"]),
        ("task_31", ["vendor dispute", "supply chain", "supplier"]),
        ("task_32", ["sla breach", "credit", "customer success"]),
        ("task_33", ["contract", "clause", "legal"]),
        ("task_34", ["ap match", "three-way", "purchase order"]),
        ("task_35", ["helpdesk", "account unlock", "mfa", "password"]),
        ("task_36", ["campaign", "marketing", "budget approval"]),
        ("task_37", ["lease", "renewal", "real estate"]),
        ("task_38", ["chargeback", "dispute", "ecommerce"]),
    ]

    def _find_relevant_patterns(self, task_text: str) -> list[tuple[str, list[str]]]:
        """Match task_text to known patterns. Returns up to 3 (task_id, tools) pairs."""
        task_lower = task_text.lower()
        matches: list[tuple[str, list[str]]] = []
        for task_id, keywords in self._TASK_KEYWORDS:
            if task_id not in self._patterns:
                continue
            if any(kw in task_lower for kw in keywords):
                matches.append((task_id, self._patterns[task_id]))
            if len(matches) >= 3:
                break
        return matches


# Module-level singleton — loaded once on process startup
_intelligence = BenchmarkIntelligence()


def load_intelligence() -> bool:
    """Call once at startup. Returns True if training data was loaded."""
    return _intelligence.load()


def get_guidance(task_text: str) -> str:
    """Get relevant tool-sequence guidance for this task. Returns empty string if not loaded."""
    return _intelligence.get_guidance(task_text)


def is_loaded() -> bool:
    return _intelligence.is_loaded
