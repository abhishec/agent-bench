#!/usr/bin/env python3
"""
Run pass^k evaluation by calling the AWS-hosted green-agent /benchmark endpoint.
This avoids local MCP session issues — everything runs within AWS.

Usage:
  python aws_pass_k.py --k 3
  python aws_pass_k.py --task task_01 --k 3
"""
from __future__ import annotations
import argparse
import asyncio
import sys
import time

import httpx

GREEN_URL = "https://benchmark.usebrainos.com"
PURPLE_URL = "https://purple.agentbench.usebrainos.com"

TASKS = [f"task_{i:02d}" for i in range(1, 16)]


async def run_once(client: httpx.AsyncClient, task_id: str, purple_url: str) -> dict:
    payload = {"task_id": task_id, "purple_url": purple_url, "difficulty": "none"}
    try:
        resp = await client.post(
            f"{GREEN_URL}/benchmark",
            json=payload,
            timeout=120.0,
        )
        resp.raise_for_status()
        data = resp.json()
        overall = data.get("scores", {}).get("overall", 0.0)
        tool_calls = data.get("tool_calls_count", 0)
        error = data.get("error")
        return {"score": overall, "tool_calls": tool_calls, "error": error}
    except Exception as e:
        return {"score": 0.0, "tool_calls": 0, "error": str(e)}


async def run_task_k_times(task_id: str, k: int, purple_url: str) -> dict:
    scores = []
    async with httpx.AsyncClient() as client:
        for i in range(k):
            result = await run_once(client, task_id, purple_url)
            score = result["score"]
            scores.append(score)
            tc = result["tool_calls"]
            err = f" ERROR: {result['error']}" if result["error"] else ""
            print(f"  run {i+1}/{k}: {score:.1f} ({tc} tool calls){err}")
            if i < k - 1:
                time.sleep(1)

    avg = sum(scores) / len(scores) if scores else 0
    pass_count = sum(1 for s in scores if s >= 70)
    return {
        "task_id": task_id,
        "pass_at_1": round(pass_count / k * 100, 1),
        "avg_score": round(avg, 1),
        "pass_k": pass_count == k,
        "scores": [round(s, 1) for s in scores],
        "k": k,
    }


async def run_all(k: int, purple_url: str):
    results = []
    for task_id in TASKS:
        print(f"\n--- {task_id} ---")
        r = await run_task_k_times(task_id, k, purple_url)
        results.append(r)
        pk = "✓" if r["pass_k"] else "✗"
        print(f"  avg={r['avg_score']} pass@1={r['pass_at_1']}% pass^{k}={pk}")

    print(f"\n{'='*65}")
    print(f"  {'Task':<12} {'pass@1':>8} {'avg':>6} {'pass^'+str(k):>8}  scores")
    print(f"  {'-'*55}")
    for r in results:
        pk = "PASS" if r["pass_k"] else "FAIL"
        scores_str = "  ".join(str(s) for s in r["scores"])
        print(f"  {r['task_id']:<12} {r['pass_at_1']:>7.1f}% {r['avg_score']:>6.1f}  {pk:>6}   {scores_str}")
    overall_pass = sum(1 for r in results if r["pass_k"])
    total = len(results)
    pct = round(overall_pass / total * 100, 1)
    print(f"  {'-'*55}")
    print(f"  Tasks pass@1≥70%: {overall_pass}/{total} ({pct}%)")
    print(f"{'='*65}\n")
    return results


def main():
    parser = argparse.ArgumentParser(description="pass^k via AWS /benchmark endpoint")
    parser.add_argument("--task", help="Single task ID (e.g. task_01)")
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--purple-url", default=PURPLE_URL)
    args = parser.parse_args()

    if args.task:
        print(f"Running {args.task} x{args.k} against {args.purple_url}")
        result = asyncio.run(run_task_k_times(args.task, args.k, args.purple_url))
        pk = "PASS" if result["pass_k"] else "FAIL"
        print(f"\npass@1={result['pass_at_1']}% avg={result['avg_score']} pass^{args.k}={pk}")
        print(f"scores: {result['scores']}")
    else:
        print(f"Running all 15 tasks x{args.k} against {args.purple_url}")
        print(f"Green: {GREEN_URL}  Purple: {args.purple_url}\n")
        asyncio.run(run_all(args.k, args.purple_url))


if __name__ == "__main__":
    main()
