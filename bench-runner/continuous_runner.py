#!/usr/bin/env python3
"""
Continuous RL-driven benchmark runner for AgentBench.
Runs forever using UCB1 scores to decide what to test.
Generates reports + training data every 4 hours.

Usage:
  python3 bench-runner/continuous_runner.py
  python3 bench-runner/continuous_runner.py --hours 24 --interval 30
  python3 bench-runner/continuous_runner.py --warmup --tasks-per-cycle 5
"""
import argparse
import datetime
import json
import sys
import time

import httpx

GREEN_URL = "https://benchmark.usebrainos.com"
PURPLE_URL = "https://purple.agentbench.usebrainos.com"

ALL_TASKS = [f"task_{i:02d}" for i in range(1, 24)]

SCENARIO_NAMES = {
    "task_01": "E-commerce Order Mgmt", "task_02": "Procurement Approval",
    "task_03": "Employee Offboarding", "task_04": "Insurance Claims",
    "task_05": "Invoice Processing", "task_06": "SLA Monitoring",
    "task_07": "Travel Rebooking", "task_08": "Compliance Audit",
    "task_09": "Subscription Mgmt", "task_10": "Payment Dispute",
    "task_11": "Accounting Recon", "task_12": "Product Catalog",
    "task_13": "Accounts Receivable", "task_14": "Incident Response",
    "task_15": "QBR", "task_16": "Retail Return", "task_17": "Retail Cancel",
    "task_18": "Retail Exchange", "task_19": "Retail Modify",
    "task_20": "Retail Address", "task_21": "Airline Booking",
    "task_22": "Airline Change", "task_23": "Airline Cancel",
}


def ts():
    return datetime.datetime.utcnow().strftime("%H:%M:%S")


def log(msg):
    print(f"[{ts()}] {msg}", flush=True)


def get_ucb_recommendations(n=5):
    try:
        r = httpx.get(f"{GREEN_URL}/rl/status", timeout=10)
        data = r.json()
        tasks = data.get("recommended_next_tasks", [])[:n]
        scores = data.get("ucb_scores", {})
        return tasks, scores
    except Exception as e:
        log(f"UCB fetch failed: {e}")
        return ALL_TASKS[:n], {}


def run_benchmark(task_id, purple_url, difficulty="none"):
    payload = {
        "task_id": task_id,
        "purple_agent_url": purple_url,
        "difficulty": difficulty,
    }
    r = httpx.post(f"{GREEN_URL}/benchmark", json=payload, timeout=180)
    data = r.json()
    scores = data.get("scores", {})
    return {
        "overall": scores.get("overall", 0.0),
        "tool_calls": data.get("tool_calls_count", 0),
        "scores": scores,
        "answer_snippet": (data.get("answer", "") or "")[:80],
        "error": data.get("error"),
    }


def trigger_report():
    try:
        r = httpx.post(f"{GREEN_URL}/report/now", timeout=30)
        data = r.json()
        return data.get("s3_json_url", data.get("s3_error", "no url"))
    except Exception as e:
        return f"report failed: {e}"


def trigger_training_export():
    try:
        r = httpx.post(f"{GREEN_URL}/training-data/export", params={"hours": 4}, timeout=60)
        data = r.json()
        return data.get("s3_url", data.get("s3_error", data.get("status", "unknown")))
    except Exception as e:
        return f"export failed: {e}"


def warmup_phase(purple_url, max_tasks=23):
    """Run each task once to seed UCB data."""
    log("=== WARMUP PHASE ===")
    log(f"Running first pass of {max_tasks} tasks to seed UCB scores...")
    results = []
    for i, task_id in enumerate(ALL_TASKS[:max_tasks]):
        name = SCENARIO_NAMES.get(task_id, task_id)
        log(f"  [{i+1}/{max_tasks}] {task_id} ({name})...")
        try:
            result = run_benchmark(task_id, purple_url)
            score = result["overall"]
            tools = result["tool_calls"]
            passed = score >= 70.0
            results.append({"task_id": task_id, "score": score, "passed": passed})
            log(f"    -> {score:.1f} | {tools} tools | {'PASS' if passed else 'FAIL'}")
        except Exception as e:
            log(f"    -> ERROR: {e}")

    passed = sum(1 for r in results if r["passed"])
    log(f"=== WARMUP COMPLETE: {passed}/{len(results)} passed ===\n")
    return results


def main():
    parser = argparse.ArgumentParser(description="Continuous RL-driven AgentBench runner")
    parser.add_argument("--hours", type=float, default=0, help="Run for N hours (0=forever)")
    parser.add_argument("--interval", type=int, default=30, help="Minutes between cycles")
    parser.add_argument("--tasks-per-cycle", type=int, default=3, help="Tasks per cycle")
    parser.add_argument("--warmup", action="store_true", help="Run warmup phase first")
    parser.add_argument("--no-warmup", action="store_true", help="Skip warmup")
    parser.add_argument("--purple-url", default=PURPLE_URL, help="Purple agent URL")
    parser.add_argument("--report-every", type=int, default=4, help="Hours between reports")
    args = parser.parse_args()

    start_time = time.time()
    last_report_time = start_time
    cycle = 0
    total_runs = 0
    total_pass = 0

    log("=" * 60)
    log("AgentBench Continuous RL Runner")
    log(f"  Purple: {args.purple_url}")
    log(f"  Interval: {args.interval} min | Tasks/cycle: {args.tasks_per_cycle}")
    log(f"  Report every: {args.report_every}h")
    log(f"  Duration: {'forever' if args.hours == 0 else f'{args.hours}h'}")
    log("=" * 60)

    # Warmup
    if args.warmup or (not args.no_warmup and args.hours == 0):
        warmup_results = warmup_phase(args.purple_url)
        total_runs += len(warmup_results)
        total_pass += sum(1 for r in warmup_results if r["passed"])

    # Main loop
    while True:
        if args.hours > 0 and (time.time() - start_time) >= args.hours * 3600:
            log(f"Time limit reached ({args.hours}h). Stopping.")
            break

        cycle += 1
        log(f"\n{'='*40}")
        log(f"CYCLE {cycle} | Total: {total_runs} runs | {total_pass}/{total_runs} pass ({100*total_pass//max(1,total_runs)}%)")

        # Get UCB recommendations
        tasks, ucb_scores = get_ucb_recommendations(args.tasks_per_cycle)
        if ucb_scores:
            top3 = [(t, ucb_scores.get(t, 0)) for t in tasks[:3]]
            log(f"UCB top picks: {', '.join(f'{t}({s:.2f})' for t, s in top3)}")

        # Run each recommended task
        for task_id in tasks:
            name = SCENARIO_NAMES.get(task_id, task_id)
            log(f"  -> {task_id} ({name})...")
            try:
                result = run_benchmark(task_id, args.purple_url)
                score = result["overall"]
                tools = result["tool_calls"]
                passed = score >= 70.0
                total_runs += 1
                if passed:
                    total_pass += 1
                status = "PASS" if passed else "FAIL"
                log(f"     {score:.1f} | {tools} tools | {status} | {result['answer_snippet'][:50]}")
                if result.get("error"):
                    log(f"     WARN Error: {result['error'][:80]}")
            except Exception as e:
                log(f"     ERROR: {e}")

        # Report every N hours
        hours_since_report = (time.time() - last_report_time) / 3600
        if hours_since_report >= args.report_every:
            log(f"\nGenerating {args.report_every}h report...")
            s3_url = trigger_report()
            log(f"   Report: {s3_url}")

            log("Exporting training data...")
            export_url = trigger_training_export()
            log(f"   Training data: {export_url}")

            last_report_time = time.time()

        # Sleep until next cycle
        sleep_sec = args.interval * 60
        log(f"\nSleeping {args.interval} min until next cycle...")
        time.sleep(sleep_sec)


if __name__ == "__main__":
    main()
