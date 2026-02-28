#!/usr/bin/env python3
"""
Adaptive benchmark runner — uses UCB1 scores from /rl/status to decide
what to test next, automatically increasing difficulty on passes.
Runs continuously until stopped (Ctrl+C).
"""
import argparse
import json
import time
import httpx

GREEN_URL = "https://benchmark.usebrainos.com"
PURPLE_URL = "https://purple.agentbench.usebrainos.com"
DIFFICULTY_PROGRESSION = ["none", "easy", "medium", "hard"]

def get_ucb_recommendations(n=3):
    resp = httpx.get(f"{GREEN_URL}/rl/status", timeout=10)
    data = resp.json()
    return data.get("recommended_next_tasks", [])[:n]

def run_task(task_id, difficulty="none", purple_url=PURPLE_URL):
    payload = {
        "task_id": task_id,
        "purple_url": purple_url,
        "difficulty": difficulty,
    }
    resp = httpx.post(f"{GREEN_URL}/benchmark", json=payload, timeout=180)
    result = resp.json()
    scores = result.get("scores", {})
    return scores.get("overall", 0.0), result.get("tool_calls_count", 0), scores

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycles", type=int, default=100, help="Number of benchmark cycles")
    parser.add_argument("--tasks-per-cycle", type=int, default=3)
    parser.add_argument("--sleep", type=int, default=30, help="Seconds between cycles")
    parser.add_argument("--purple-url", default=PURPLE_URL)
    args = parser.parse_args()

    task_difficulty: dict[str, str] = {}  # tracks current difficulty per task

    print(f"Starting adaptive runner: {args.cycles} cycles, {args.tasks_per_cycle} tasks/cycle")
    print(f"Purple: {args.purple_url}")

    for cycle in range(args.cycles):
        print(f"\n=== Cycle {cycle+1}/{args.cycles} ===")
        try:
            tasks = get_ucb_recommendations(args.tasks_per_cycle)
        except Exception as e:
            print(f"UCB fetch failed: {e}, using task_01")
            tasks = ["task_01"]

        for task_id in tasks:
            diff = task_difficulty.get(task_id, "none")
            print(f"  Running {task_id} (difficulty={diff})...", end=" ", flush=True)
            try:
                score, tools, scores = run_task(task_id, diff, args.purple_url)
                passed = score >= 70.0
                print(f"score={score:.1f} tools={tools} {'PASS' if passed else 'FAIL'}")
                if passed and diff != DIFFICULTY_PROGRESSION[-1]:
                    next_diff = DIFFICULTY_PROGRESSION[DIFFICULTY_PROGRESSION.index(diff) + 1]
                    task_difficulty[task_id] = next_diff
                    print(f"    -> Upgrading {task_id} to difficulty={next_diff}")
                elif not passed:
                    task_difficulty[task_id] = "none"
            except Exception as e:
                print(f"ERROR: {e}")

        # Generate report every 10 cycles
        if (cycle + 1) % 10 == 0:
            try:
                resp = httpx.post(f"{GREEN_URL}/report/now", timeout=30)
                data = resp.json()
                print(f"\n  Report: {data.get('total_runs')} runs, {data.get('pass_rate',0)*100:.0f}% pass rate")
                print(f"     S3: {data.get('s3_json_url', 'N/A')}")
            except Exception as e:
                print(f"  Report generation failed: {e}")

        if cycle < args.cycles - 1:
            print(f"  Sleeping {args.sleep}s...")
            time.sleep(args.sleep)

    print("\nAdaptive runner complete.")

if __name__ == "__main__":
    main()
