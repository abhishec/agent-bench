#!/usr/bin/env python3
"""
Competition Stress Test — simulates exactly how AgentBeats will run assessments.

Sends EvalRequests via the A2A message/send protocol (same as AgentBeats platform),
cycles through all 38 scenarios at multiple difficulty levels, logs every result
to a local JSONL file, and prints a live leaderboard every N minutes.

Usage:
  # Run for 4 hours, full 38-task rounds, all difficulties
  python3 bench-runner/competition_stress_test.py --hours 4

  # Quick smoke test (1 round, none difficulty)
  python3 bench-runner/competition_stress_test.py --rounds 1 --difficulty none

  # Tonight's run — all difficulties, forever until you Ctrl-C
  python3 bench-runner/competition_stress_test.py --hours 8 --log-file /tmp/stress_results.jsonl
"""
from __future__ import annotations
import argparse
import datetime
import json
import os
import sys
import time
import uuid
from collections import defaultdict
from pathlib import Path

import httpx

# ── Config ────────────────────────────────────────────────────────────────────
GREEN_URL  = os.getenv("GREEN_URL",  "https://benchmark.usebrainos.com")
PURPLE_URL = os.getenv("PURPLE_URL", "https://purple.agentbench.usebrainos.com")
BENCH_KEY  = os.getenv("BENCHMARK_API_KEY", "")  # fetched below if empty

# ── What the competition is likely to test ────────────────────────────────────
# All 38 tasks grouped by priority:
#   HIGH  — tau-bench style domains AgentBeats focuses on (airline, retail, e-comm)
#   MED   — business process domains (banking, HR, healthcare, supply chain)
#   ALL   — every single scenario

COMPETITION_TASKS = {
    "tau_bench_style": [          # most likely competition focus
        "task_16", "task_17", "task_18", "task_19", "task_20",  # retail (5)
        "task_21", "task_22", "task_23",                          # airline (3)
        "task_01", "task_12",                                     # e-commerce (2)
    ],
    "business_process": [         # our unique strength
        "task_24", "task_25",     # banking
        "task_26", "task_27",     # HR
        "task_28", "task_29",     # healthcare
        "task_30", "task_31",     # supply chain
        "task_32",                # customer success
        "task_33",                # legal
        "task_34",                # finance AP
        "task_35",                # IT helpdesk
        "task_36",                # marketing
        "task_37",                # real estate
        "task_38",                # e-commerce chargeback
    ],
    "escalation_heavy": [         # tests policy compliance + escalation dimension
        "task_02", "task_04", "task_05", "task_06",
        "task_07", "task_08", "task_09", "task_10",
        "task_13", "task_32", "task_33", "task_37",
    ],
    "all_38": [f"task_{i:02d}" for i in range(1, 39)],
}

# Competition difficulty progression — start easy, stress with hard
DIFFICULTY_SCHEDULE = ["none", "easy", "medium", "hard", "adversarial"]

PASS_THRESHOLD = 70.0

# ── Helpers ───────────────────────────────────────────────────────────────────
def ts():
    return datetime.datetime.utcnow().strftime("%H:%M:%S UTC")

def log(msg, level="INFO"):
    icon = {"INFO": "·", "PASS": "✅", "FAIL": "❌", "WARN": "⚠️", "HEAD": "═"}.get(level, "·")
    print(f"[{ts()}] {icon} {msg}", flush=True)

def get_bench_key() -> str:
    """Fetch BENCHMARK_API_KEY from SSM if not set in env."""
    global BENCH_KEY
    if BENCH_KEY:
        return BENCH_KEY
    try:
        import subprocess
        result = subprocess.run([
            "aws", "ssm", "get-parameter",
            "--name", "/agentbench/benchmark-api-key",
            "--with-decryption",
            "--query", "Parameter.Value",
            "--output", "text"
        ], capture_output=True, text=True, timeout=10)
        BENCH_KEY = result.stdout.strip()
        if BENCH_KEY:
            log(f"Fetched BENCHMARK_API_KEY from SSM (len={len(BENCH_KEY)})")
        return BENCH_KEY
    except Exception as e:
        log(f"Could not fetch API key from SSM: {e}", "WARN")
        return ""


# ── Core: send EvalRequest exactly as AgentBeats will ─────────────────────────
def run_agentbeats_eval(
    task_ids: list[str],
    purple_url: str,
    difficulty: str = "none",
    timeout: int = 300,
) -> dict:
    """
    Send an EvalRequest via A2A message/send — identical to what AgentBeats platform sends.
    Green receives it, runs purple on each task, scores, returns artifact.
    """
    eval_request = json.dumps({
        "participants": {"agent": purple_url},
        "config": {
            "task_ids": task_ids,
            "difficulty": difficulty,
        }
    })

    a2a_payload = {
        "jsonrpc": "2.0",
        "method": "message/send",
        "id": str(uuid.uuid4()),
        "params": {
            "message": {
                "messageId": str(uuid.uuid4()),
                "contextId":  str(uuid.uuid4()),
                "parts": [{"kind": "text", "text": eval_request}],
            }
        }
    }

    key = get_bench_key()
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-API-Key"] = key

    resp = httpx.post(
        f"{GREEN_URL}/",
        json=a2a_payload,
        headers=headers,
        timeout=timeout,
    )
    resp.raise_for_status()
    body = resp.json()

    # Extract evaluation_result from A2A artifact
    artifacts = body.get("result", {}).get("artifacts", [])
    for artifact in artifacts:
        if artifact.get("name") == "evaluation_result":
            for part in artifact.get("parts", []):
                text = part.get("text", "")
                if text:
                    return json.loads(text)

    return {"error": "No evaluation_result artifact", "raw": body}


# ── Single-task benchmark (direct /benchmark endpoint) ────────────────────────
def run_single_task(task_id: str, purple_url: str, difficulty: str = "none") -> dict:
    """Direct /benchmark call — faster for per-task testing."""
    key = get_bench_key()
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-API-Key"] = key

    resp = httpx.post(
        f"{GREEN_URL}/benchmark",
        json={"task_id": task_id, "purple_url": purple_url, "difficulty": difficulty},
        headers=headers,
        timeout=180,
    )
    resp.raise_for_status()
    data = resp.json()
    scores = data.get("scores", {})
    return {
        "task_id": task_id,
        "difficulty": difficulty,
        "overall": scores.get("overall", 0.0),
        "functional": scores.get("functional", 0.0),
        "policy_compliance": scores.get("policy_compliance", 0.0),
        "tool_sequence": scores.get("tool_sequence", 0.0),
        "escalation": scores.get("escalation", 0.0),
        "tool_calls": data.get("tool_calls_count", 0),
        "passed": scores.get("overall", 0.0) >= PASS_THRESHOLD,
        "answer": (data.get("answer", "") or "")[:120],
        "error": data.get("error"),
    }


# ── Stats accumulator ─────────────────────────────────────────────────────────
class RunStats:
    def __init__(self):
        self.results: list[dict] = []
        self.start_time = time.time()

    def add(self, r: dict):
        r["ts"] = datetime.datetime.utcnow().isoformat()
        self.results.append(r)

    def summary_by_task(self) -> dict:
        by_task = defaultdict(list)
        for r in self.results:
            by_task[r["task_id"]].append(r)
        out = {}
        for tid, runs in sorted(by_task.items()):
            scores = [x["overall"] for x in runs]
            out[tid] = {
                "runs": len(runs),
                "pass_rate": sum(1 for x in runs if x["passed"]) / len(runs),
                "avg_score": sum(scores) / len(scores),
                "min_score": min(scores),
                "max_score": max(scores),
                "avg_tools": sum(x.get("tool_calls",0) for x in runs) / len(runs),
            }
        return out

    def summary_by_difficulty(self) -> dict:
        by_diff = defaultdict(list)
        for r in self.results:
            by_diff[r.get("difficulty","none")].append(r)
        out = {}
        for diff, runs in sorted(by_diff.items()):
            scores = [x["overall"] for x in runs]
            out[diff] = {
                "runs": len(runs),
                "pass_rate": sum(1 for x in runs if x["passed"]) / len(runs),
                "avg_score": sum(scores) / len(scores),
            }
        return out

    def print_leaderboard(self):
        elapsed = (time.time() - self.start_time) / 60
        total = len(self.results)
        passed = sum(1 for r in self.results if r["passed"])
        avg = sum(r["overall"] for r in self.results) / max(1, total)

        print("\n" + "═"*70, flush=True)
        print(f"  LEADERBOARD  |  {elapsed:.0f} min elapsed  |  {total} runs  |  {passed}/{total} PASS  |  avg={avg:.1f}", flush=True)
        print("═"*70, flush=True)

        by_task = self.summary_by_task()
        failing = [(tid, s) for tid, s in by_task.items() if s["pass_rate"] < 1.0]
        passing = [(tid, s) for tid, s in by_task.items() if s["pass_rate"] == 1.0]

        if failing:
            print(f"\n  ❌ NEEDS WORK ({len(failing)} tasks):", flush=True)
            for tid, s in sorted(failing, key=lambda x: x[1]["avg_score"]):
                bar = "█" * int(s["pass_rate"] * 10) + "░" * (10 - int(s["pass_rate"] * 10))
                print(f"    {tid}  [{bar}] {s['pass_rate']*100:.0f}%  avg={s['avg_score']:.1f}  runs={s['runs']}", flush=True)

        if passing:
            tids = [t for t, _ in passing]
            print(f"\n  ✅ SOLID ({len(passing)} tasks): {tids}", flush=True)

        print("\n  By difficulty:", flush=True)
        for diff, s in self.summary_by_difficulty().items():
            mult = {"none":1.0,"easy":1.1,"medium":1.2,"hard":1.4,"adversarial":1.6}.get(diff,1.0)
            print(f"    {diff:<12} {s['pass_rate']*100:.0f}% pass  avg={s['avg_score']:.1f}  ({s['runs']} runs)  [×{mult}]", flush=True)

        print("═"*70 + "\n", flush=True)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Competition stress test for AgentBench")
    parser.add_argument("--hours",      type=float, default=4,    help="Run duration in hours (default 4)")
    parser.add_argument("--rounds",     type=int,   default=0,    help="Fixed number of full rounds (overrides --hours)")
    parser.add_argument("--difficulty", default="all",            help="none|easy|medium|hard|adversarial|all (default all)")
    parser.add_argument("--task-set",   default="all_38",         help="tau_bench_style|business_process|escalation_heavy|all_38 (default all_38)")
    parser.add_argument("--mode",       default="individual",     help="individual (per-task /benchmark) | agentbeats (A2A EvalRequest)")
    parser.add_argument("--purple-url", default=PURPLE_URL)
    parser.add_argument("--log-file",   default="/tmp/stress_results.jsonl", help="JSONL output file")
    parser.add_argument("--leaderboard-every", type=int, default=10, help="Print leaderboard every N minutes")
    args = parser.parse_args()

    # Resolve task set
    tasks = COMPETITION_TASKS.get(args.task_set, COMPETITION_TASKS["all_38"])

    # Resolve difficulties
    if args.difficulty == "all":
        difficulties = DIFFICULTY_SCHEDULE
    else:
        difficulties = [d.strip() for d in args.difficulty.split(",")]

    log("="*70, "HEAD")
    log(f"Competition Stress Test — {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    log(f"  Green:      {GREEN_URL}")
    log(f"  Purple:     {args.purple_url}")
    log(f"  Task set:   {args.task_set} ({len(tasks)} tasks)")
    log(f"  Difficulty: {difficulties}")
    log(f"  Mode:       {args.mode}")
    log(f"  Duration:   {f'{args.hours}h' if args.rounds == 0 else f'{args.rounds} rounds'}")
    log(f"  Log file:   {args.log_file}")
    log("="*70, "HEAD")

    # Verify green + purple are up
    log("Checking green agent health...")
    try:
        r = httpx.get(f"{GREEN_URL}/health", timeout=10)
        log(f"  Green: {r.json()}")
    except Exception as e:
        log(f"  Green UNREACHABLE: {e}", "WARN")
        sys.exit(1)

    log("Checking purple agent health...")
    try:
        r = httpx.get(f"{args.purple_url}/health", timeout=10)
        log(f"  Purple: {r.json()}")
    except Exception as e:
        log(f"  Purple UNREACHABLE: {e}", "WARN")

    # Pre-fetch API key
    get_bench_key()

    stats = RunStats()
    log_path = Path(args.log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    start_time = time.time()
    deadline = start_time + args.hours * 3600
    round_num = 0
    last_leaderboard = start_time

    try:
        while True:
            # Check termination
            if args.rounds > 0 and round_num >= args.rounds:
                log(f"Completed {args.rounds} rounds. Stopping.")
                break
            if args.rounds == 0 and time.time() >= deadline:
                log(f"{args.hours}h elapsed. Stopping.")
                break

            round_num += 1
            # Cycle through difficulties round-robin
            diff = difficulties[(round_num - 1) % len(difficulties)]

            elapsed_min = (time.time() - start_time) / 60
            log(f"\nROUND {round_num} | difficulty={diff} | {elapsed_min:.0f} min elapsed | {len(stats.results)} runs so far", "HEAD")

            if args.mode == "agentbeats":
                # Send all tasks in one EvalRequest (exactly like AgentBeats)
                log(f"  Sending A2A EvalRequest: {len(tasks)} tasks at difficulty={diff}...")
                try:
                    result = run_agentbeats_eval(tasks, args.purple_url, diff, timeout=600)
                    if "error" in result:
                        log(f"  EvalRequest error: {result['error']}", "WARN")
                    else:
                        per_task = result.get("results", [])
                        for tr in per_task:
                            row = {
                                "round": round_num,
                                "task_id": tr.get("task_id"),
                                "difficulty": diff,
                                "overall": tr.get("scores", {}).get("overall", 0),
                                "functional": tr.get("scores", {}).get("functional", 0),
                                "policy_compliance": tr.get("scores", {}).get("policy_compliance", 0),
                                "tool_sequence": tr.get("scores", {}).get("tool_sequence", 0),
                                "escalation": tr.get("scores", {}).get("escalation", 0),
                                "tool_calls": tr.get("tool_calls_count", 0),
                                "passed": tr.get("scores", {}).get("overall", 0) >= PASS_THRESHOLD,
                            }
                            stats.add(row)
                            with open(log_path, "a") as f:
                                f.write(json.dumps(row) + "\n")

                        passed = sum(1 for r in per_task if r.get("scores",{}).get("overall",0) >= PASS_THRESHOLD)
                        log(f"  Round result: {passed}/{len(per_task)} PASS  pass_rate={result.get('pass_rate',0):.2f}")
                except Exception as e:
                    log(f"  EvalRequest failed: {e}", "WARN")

            else:
                # Individual /benchmark calls — more granular, easier to debug
                round_pass = 0
                for i, task_id in enumerate(tasks):
                    # Check time limit between tasks
                    if args.rounds == 0 and time.time() >= deadline:
                        break

                    try:
                        result = run_single_task(task_id, args.purple_url, diff)
                        stats.add({**result, "round": round_num})
                        with open(log_path, "a") as f:
                            f.write(json.dumps({**result, "round": round_num}) + "\n")

                        icon = "✅" if result["passed"] else "❌"
                        score = result["overall"]
                        tools = result["tool_calls"]
                        if result["passed"]:
                            round_pass += 1
                        print(
                            f"  [{i+1:>2}/{len(tasks)}] {task_id}  {icon}  "
                            f"score={score:>5.1f}  tools={tools:>2}  diff={diff}  "
                            f"{result['answer'][:40]}",
                            flush=True
                        )
                        if result.get("error"):
                            log(f"    Error: {result['error'][:100]}", "WARN")

                    except httpx.TimeoutException:
                        log(f"  [{task_id}] TIMEOUT after 180s", "WARN")
                    except Exception as e:
                        log(f"  [{task_id}] ERROR: {e}", "WARN")

                log(f"Round {round_num} done: {round_pass}/{len(tasks)} PASS at difficulty={diff}")

            # Print leaderboard periodically
            if (time.time() - last_leaderboard) >= args.leaderboard_every * 60:
                stats.print_leaderboard()
                last_leaderboard = time.time()

    except KeyboardInterrupt:
        log("\nInterrupted by user.")

    # Final report
    log("\n" + "="*70, "HEAD")
    log("FINAL REPORT")
    log("="*70, "HEAD")
    stats.print_leaderboard()

    total = len(stats.results)
    passed = sum(1 for r in stats.results if r["passed"])
    elapsed = (time.time() - start_time) / 3600

    log(f"Total runs:    {total}")
    log(f"Total passed:  {passed}/{total} ({100*passed//max(1,total)}%)")
    log(f"Elapsed:       {elapsed:.2f}h")
    log(f"Results saved: {log_path}  ({log_path.stat().st_size // 1024}KB)" if log_path.exists() else f"Log: {log_path}")

    # Print worst performers
    by_task = stats.summary_by_task()
    worst = sorted(by_task.items(), key=lambda x: x[1]["avg_score"])[:5]
    if worst:
        log("\nTop 5 weakest tasks (fix before competition):")
        for tid, s in worst:
            log(f"  {tid}  avg={s['avg_score']:.1f}  pass_rate={s['pass_rate']*100:.0f}%  runs={s['runs']}")

    log(f"\nLog file for deep analysis: {log_path}")
    log("Run: python3 bench-runner/analyze_stress_test.py " + str(log_path))


if __name__ == "__main__":
    main()
