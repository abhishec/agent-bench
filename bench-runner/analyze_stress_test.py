#!/usr/bin/env python3
"""
Analyze results from competition_stress_test.py JSONL output.

Usage:
  python3 bench-runner/analyze_stress_test.py /tmp/stress_results.jsonl
  python3 bench-runner/analyze_stress_test.py /tmp/stress_results.jsonl --markdown
"""
from __future__ import annotations
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

PASS_THRESHOLD = 70.0

DOMAIN_MAP = {
    "task_01": "e-commerce",   "task_02": "procurement",  "task_03": "hr",
    "task_04": "insurance",    "task_05": "finance",       "task_06": "operations",
    "task_07": "travel",       "task_08": "compliance",    "task_09": "saas",
    "task_10": "finance",      "task_11": "accounting",    "task_12": "e-commerce",
    "task_13": "accounting",   "task_14": "operations",    "task_15": "strategy",
    "task_16": "retail",       "task_17": "retail",        "task_18": "retail",
    "task_19": "retail",       "task_20": "retail",        "task_21": "airline",
    "task_22": "airline",      "task_23": "airline",       "task_24": "banking",
    "task_25": "banking",      "task_26": "hr",            "task_27": "hr",
    "task_28": "healthcare",   "task_29": "healthcare",    "task_30": "supply_chain",
    "task_31": "supply_chain", "task_32": "customer_success","task_33": "legal",
    "task_34": "finance",      "task_35": "it_helpdesk",   "task_36": "marketing",
    "task_37": "real_estate",  "task_38": "chargeback",
}

DIFFICULTY_MULT = {"none":1.0,"easy":1.1,"medium":1.2,"hard":1.4,"adversarial":1.6}


def load_results(path: Path) -> list[dict]:
    results = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return results


def analyze(results: list[dict], markdown: bool = False):
    total = len(results)
    if total == 0:
        print("No results found.")
        return

    passed = sum(1 for r in results if r.get("passed"))
    avg_score = sum(r.get("overall", 0) for r in results) / total
    avg_tools = sum(r.get("tool_calls", 0) for r in results) / total

    sep = "---" if markdown else "─"*70
    h2 = "##" if markdown else "══"

    print(f"\n{h2} Competition Stress Test Analysis\n")
    print(f"**Total runs:** {total}  |  **Pass rate:** {passed}/{total} ({100*passed//total}%)  |  **Avg score:** {avg_score:.1f}  |  **Avg tool calls:** {avg_tools:.1f}\n")

    # ── By task ──────────────────────────────────────────────────────────────
    by_task: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_task[r["task_id"]].append(r)

    print(f"\n{h2} Per-Task Performance\n")
    if markdown:
        print("| Task | Domain | Runs | Pass% | Avg | Min | Max | Avg Tools | Status |")
        print("|------|--------|------|-------|-----|-----|-----|-----------|--------|")

    task_summary = []
    for tid in sorted(by_task.keys()):
        runs = by_task[tid]
        scores = [r.get("overall", 0) for r in runs]
        n_pass = sum(1 for r in runs if r.get("passed"))
        pass_rate = n_pass / len(runs)
        avg = sum(scores) / len(scores)
        tools_avg = sum(r.get("tool_calls", 0) for r in runs) / len(runs)
        domain = DOMAIN_MAP.get(tid, "?")
        status = "✅ SOLID" if pass_rate == 1.0 else ("⚠️ FLAKY" if pass_rate >= 0.5 else "❌ WEAK")
        task_summary.append((tid, domain, len(runs), pass_rate, avg, min(scores), max(scores), tools_avg, status))

    task_summary.sort(key=lambda x: x[4])  # sort by avg score asc (worst first)

    for tid, domain, n, pr, avg, mn, mx, tools, status in task_summary:
        if markdown:
            print(f"| {tid} | {domain} | {n} | {pr*100:.0f}% | {avg:.1f} | {mn:.1f} | {mx:.1f} | {tools:.1f} | {status} |")
        else:
            bar = "█" * int(pr * 20) + "░" * (20 - int(pr * 20))
            print(f"  {tid}  [{bar}] {pr*100:>3.0f}%  avg={avg:>5.1f}  min={mn:>5.1f}  tools={tools:.1f}  {domain:<15} {status}")

    # ── By difficulty ─────────────────────────────────────────────────────────
    by_diff: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_diff[r.get("difficulty","none")].append(r)

    print(f"\n{h2} By Difficulty\n")
    if markdown:
        print("| Difficulty | Multiplier | Runs | Pass% | Avg Score | Effective Score |")
        print("|------------|------------|------|-------|-----------|-----------------|")

    for diff in ["none","easy","medium","hard","adversarial"]:
        runs = by_diff.get(diff, [])
        if not runs:
            continue
        scores = [r.get("overall", 0) for r in runs]
        pr = sum(1 for r in runs if r.get("passed")) / len(runs)
        avg = sum(scores) / len(scores)
        mult = DIFFICULTY_MULT.get(diff, 1.0)
        effective = avg * mult
        if markdown:
            print(f"| {diff} | ×{mult} | {len(runs)} | {pr*100:.0f}% | {avg:.1f} | {effective:.1f} |")
        else:
            icon = "✅" if pr >= 0.8 else ("⚠️" if pr >= 0.5 else "❌")
            print(f"  {icon} {diff:<12} ×{mult}  {pr*100:>3.0f}% pass  avg={avg:.1f}  effective={effective:.1f}  ({len(runs)} runs)")

    # ── By domain ─────────────────────────────────────────────────────────────
    by_domain: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        dom = DOMAIN_MAP.get(r["task_id"], "unknown")
        by_domain[dom].append(r)

    print(f"\n{h2} By Domain\n")
    if markdown:
        print("| Domain | Runs | Pass% | Avg Score |")
        print("|--------|------|-------|-----------|")

    dom_summary = []
    for dom, runs in by_domain.items():
        scores = [r.get("overall", 0) for r in runs]
        pr = sum(1 for r in runs if r.get("passed")) / len(runs)
        avg = sum(scores) / len(scores)
        dom_summary.append((dom, len(runs), pr, avg))
    dom_summary.sort(key=lambda x: x[2])  # worst first

    for dom, n, pr, avg in dom_summary:
        if markdown:
            print(f"| {dom} | {n} | {pr*100:.0f}% | {avg:.1f} |")
        else:
            icon = "✅" if pr >= 0.8 else ("⚠️" if pr >= 0.5 else "❌")
            print(f"  {icon} {dom:<20} {pr*100:>3.0f}% pass  avg={avg:.1f}  ({n} runs)")

    # ── Score dimensions ──────────────────────────────────────────────────────
    print(f"\n{h2} Score Dimensions (avg across all runs)\n")
    dims = ["overall", "functional", "policy_compliance", "tool_sequence", "escalation"]
    for dim in dims:
        vals = [r.get(dim, 0) for r in results if dim in r]
        if vals:
            avg = sum(vals) / len(vals)
            bar = "█" * int(avg / 5) + "░" * (20 - int(avg / 5))
            weakness = " ← WEAKEST" if avg < 50 else (" ← needs work" if avg < 70 else "")
            if markdown:
                print(f"- **{dim}**: {avg:.1f}/100")
            else:
                print(f"  {dim:<22} [{bar}] {avg:.1f}/100{weakness}")

    # ── Action items ──────────────────────────────────────────────────────────
    print(f"\n{h2} Action Items Before Competition\n")

    failing_tasks = [(tid, s) for tid, _, n, pr, s, mn, mx, _, _ in task_summary if pr < 0.8]
    if failing_tasks:
        print("  Fix these tasks first (< 80% pass rate):")
        for tid, avg in failing_tasks[:10]:
            domain = DOMAIN_MAP.get(tid, "?")
            print(f"    {tid} ({domain}) avg={avg:.1f}")
    else:
        print("  ✅ All tasks at ≥ 80% pass rate. Focus on adversarial difficulty.")

    hard_adv = [r for r in results if r.get("difficulty") in ("hard", "adversarial")]
    if hard_adv:
        hard_pr = sum(1 for r in hard_adv if r.get("passed")) / len(hard_adv)
        if hard_pr < 0.7:
            print(f"\n  ⚠️  Hard/adversarial pass rate = {hard_pr*100:.0f}% — purple needs more resilience")

    print()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("log_file", help="JSONL file from competition_stress_test.py")
    parser.add_argument("--markdown", action="store_true", help="Output as Markdown tables")
    args = parser.parse_args()

    path = Path(args.log_file)
    if not path.exists():
        print(f"File not found: {path}")
        sys.exit(1)

    results = load_results(path)
    print(f"Loaded {len(results)} results from {path}")
    analyze(results, args.markdown)


if __name__ == "__main__":
    main()
