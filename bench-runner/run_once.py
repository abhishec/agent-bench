#!/usr/bin/env python3
"""Run all benchmark tasks once and show a summary table."""
import httpx
import json
import sys
import time

GREEN_URL = "https://benchmark.usebrainos.com"
PURPLE_URL = "https://purple.agentbench.usebrainos.com"


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--tasks", nargs="*", default=None, help="Task IDs to run (default: all)")
    p.add_argument("--purple-url", default=PURPLE_URL)
    p.add_argument("--difficulty", default="none")
    args = p.parse_args()

    # Get registered tasks from green
    try:
        r = httpx.get(f"{GREEN_URL}/scenarios", timeout=10)
        all_tasks = r.json().get("tasks", [f"task_{i:02d}" for i in range(1, 24)])
        # /scenarios returns a list of dicts with task_id keys
        if all_tasks and isinstance(all_tasks[0], dict):
            all_tasks = [t["task_id"] for t in all_tasks]
    except Exception:
        all_tasks = [f"task_{i:02d}" for i in range(1, 24)]

    tasks = args.tasks or all_tasks
    results = []
    print(f"Running {len(tasks)} tasks | difficulty={args.difficulty}")
    print("-" * 70)

    for task_id in tasks:
        sys.stdout.write(f"  {task_id:8s} ... ")
        sys.stdout.flush()
        try:
            t0 = time.time()
            r = httpx.post(f"{GREEN_URL}/benchmark", json={
                "task_id": task_id,
                "purple_agent_url": args.purple_url,
                "difficulty": args.difficulty,
            }, timeout=180)
            elapsed = time.time() - t0
            data = r.json()
            scores = data.get("scores", {})
            overall = scores.get("overall", 0.0)
            tools = data.get("tool_calls_count", 0)
            passed = overall >= 70.0
            results.append({"task_id": task_id, "score": overall, "tools": tools, "passed": passed, "elapsed": elapsed})
            print(f"{overall:5.1f}  {tools:3d} tools  {'PASS' if passed else 'FAIL'}  ({elapsed:.0f}s)")
        except Exception as e:
            print(f"ERROR: {e}")
            results.append({"task_id": task_id, "score": 0, "tools": 0, "passed": False, "elapsed": 0})

    print("-" * 70)
    passed = sum(1 for r in results if r["passed"])
    avg = sum(r["score"] for r in results) / len(results) if results else 0
    print(f"TOTAL: {passed}/{len(results)} pass ({100*passed//max(1,len(results))}%)  avg={avg:.1f}")
    print(f"FAIL: {[r['task_id'] for r in results if not r['passed']]}")


if __name__ == "__main__":
    main()
