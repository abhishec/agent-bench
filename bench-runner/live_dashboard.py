#!/usr/bin/env python3
"""
live_dashboard.py — Real-time AgentBench activity monitor.

Shows live messages between green (judge) and purple (competitor) agents,
pulled from CloudWatch logs + the green agent's live API endpoints.

Usage:
  python3 bench-runner/live_dashboard.py
  python3 bench-runner/live_dashboard.py --green-url https://benchmark.usebrainos.com
  python3 bench-runner/live_dashboard.py --poll-secs 3

Press Ctrl+C to exit.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any

import boto3

# ── ANSI colours ─────────────────────────────────────────────────────────────
RESET   = "\033[0m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
GREEN   = "\033[92m"
RED     = "\033[91m"
YELLOW  = "\033[93m"
CYAN    = "\033[96m"
MAGENTA = "\033[95m"
BLUE    = "\033[94m"
WHITE   = "\033[97m"
BG_DARK = "\033[48;5;234m"

def clr(text: str, *codes: str) -> str:
    return "".join(codes) + str(text) + RESET


# ── Config ───────────────────────────────────────────────────────────────────
DEFAULT_GREEN_URL    = "https://benchmark.usebrainos.com"
GREEN_LOG_GROUP      = "/ecs/agentbench-green"
PURPLE_LOG_GROUP     = "/ecs/agentbench-purple"
REGION               = "us-east-1"
ACTIVITY_QUEUE_SIZE  = 40       # lines kept in the scrolling feed
HEADER_WIDTH         = 100


# ── CloudWatch log tailing ────────────────────────────────────────────────────
def _latest_streams(cw, log_group: str, n: int = 3) -> list[str]:
    """Return the n most-recently active stream names."""
    resp = cw.describe_log_streams(
        logGroupName=log_group,
        orderBy="LastEventTime",
        descending=True,
        limit=n,
    )
    return [s["logStreamName"] for s in resp.get("logStreams", [])]


def _fetch_new_events(cw, log_group: str, stream: str, since_ms: int) -> list[dict]:
    """Fetch log events newer than since_ms (epoch ms)."""
    try:
        resp = cw.get_log_events(
            logGroupName=log_group,
            logStreamName=stream,
            startTime=since_ms + 1,
            startFromHead=True,
        )
        return resp.get("events", [])
    except Exception:
        return []


# ── Event parsing ─────────────────────────────────────────────────────────────
def _ts(epoch_ms: int) -> str:
    dt = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)
    return dt.strftime("%H:%M:%S")


RE_BENCH_START  = re.compile(r"\[BENCH START\] task=(\S+) diff=(\S+) sid=(\S+)")
RE_BENCH_SCORE  = re.compile(r"\[BENCH SCORE\] task=(\S+) diff=(\S+) sid=(\S+) overall=(\S+) (\S+) func=(\S+) policy=(\S+) tools=(\S+)")
RE_BENCH_ERROR  = re.compile(r"\[BENCH ERROR\] task=(\S+) diff=(\S+) sid=(\S+) error=(.+)")
RE_MCP_CALL     = re.compile(r"\[MCP CALL\] tool=(\S+) sid=(\S+)")
RE_HTTP_BENCH   = re.compile(r'"(POST|GET) /benchmark[^ ]* HTTP')
RE_HTTP_A2A     = re.compile(r'"POST / HTTP')
RE_FASTAPI_ERR  = re.compile(r"ERROR:")


def parse_green_event(msg: str, ts_ms: int) -> dict | None:
    """Parse a green-agent log line into a structured event dict."""

    m = RE_BENCH_START.search(msg)
    if m:
        return {
            "kind": "task_start",
            "task": m.group(1), "diff": m.group(2), "sid": m.group(3),
            "ts": _ts(ts_ms), "ts_ms": ts_ms,
            "display": (
                clr(f"▶ TASK START", BOLD, CYAN) +
                f"  {clr(m.group(1), BOLD, WHITE)}" +
                f"  diff={clr(m.group(2), YELLOW)}" +
                f"  sid={clr(m.group(3), DIM)}"
            ),
        }

    m = RE_BENCH_SCORE.search(msg)
    if m:
        task, diff, sid = m.group(1), m.group(2), m.group(3)
        overall, result = m.group(4), m.group(5)
        func, policy, n_tools = m.group(6), m.group(7), m.group(8)
        passed = result == "PASS"
        colour = GREEN if passed else RED
        icon   = "✅" if passed else "❌"
        return {
            "kind": "score",
            "task": task, "diff": diff, "sid": sid,
            "overall": float(overall), "passed": passed,
            "func": float(func), "policy": float(policy),
            "ts": _ts(ts_ms), "ts_ms": ts_ms,
            "display": (
                clr(f"{icon} SCORE", BOLD, colour) +
                f"  {clr(task, BOLD, WHITE)}" +
                f"  {clr(overall, BOLD, colour)}/100" +
                f"  func={func} policy={policy} tools={n_tools}" +
                f"  diff={clr(diff, YELLOW)}"
            ),
        }

    m = RE_BENCH_ERROR.search(msg)
    if m:
        return {
            "kind": "error",
            "task": m.group(1), "diff": m.group(2), "sid": m.group(3),
            "error": m.group(4),
            "ts": _ts(ts_ms), "ts_ms": ts_ms,
            "display": (
                clr("⚠ ERROR", BOLD, RED) +
                f"  {clr(m.group(1), BOLD, WHITE)}" +
                f"  {clr(m.group(4)[:80], DIM)}"
            ),
        }

    m = RE_MCP_CALL.search(msg)
    if m:
        return {
            "kind": "mcp_call",
            "tool": m.group(1), "sid": m.group(2),
            "ts": _ts(ts_ms), "ts_ms": ts_ms,
            "display": (
                clr("  ⚙ MCP", BLUE) +
                f"  {clr(m.group(1), BOLD, BLUE)}" +
                f"  sid={clr(m.group(2), DIM)}"
            ),
        }

    # Catch FastAPI errors worth surfacing
    if RE_FASTAPI_ERR.search(msg) and "Exception" in msg:
        return {
            "kind": "server_error",
            "ts": _ts(ts_ms), "ts_ms": ts_ms,
            "display": clr(f"⚠ SERVER ERR  {msg[:100]}", RED),
        }

    return None


def parse_purple_event(msg: str, ts_ms: int) -> dict | None:
    """Parse a purple-agent log line into a structured event dict."""

    # Purple receives A2A tasks from green
    if RE_HTTP_A2A.search(msg):
        return {
            "kind": "purple_a2a",
            "ts": _ts(ts_ms), "ts_ms": ts_ms,
            "display": clr("  🟣 PURPLE received A2A task", MAGENTA),
        }

    # FastAPI errors on purple
    if RE_FASTAPI_ERR.search(msg) and len(msg) < 200:
        return {
            "kind": "purple_error",
            "ts": _ts(ts_ms), "ts_ms": ts_ms,
            "display": clr(f"  🟣⚠ PURPLE ERR  {msg[:100]}", MAGENTA),
        }

    return None


# ── Stats tracker ─────────────────────────────────────────────────────────────
class Stats:
    def __init__(self):
        self.task_runs: dict[str, list[float]] = defaultdict(list)  # task → [scores]
        self.task_passed: dict[str, int] = defaultdict(int)
        self.task_total: dict[str, int] = defaultdict(int)
        self.total_runs = 0
        self.total_passed = 0
        self.recent_scores: deque[float] = deque(maxlen=50)
        self.mcp_calls = 0
        self.errors = 0
        self.start_time = time.time()
        self.active_tasks: dict[str, str] = {}   # sid → task

    def record_start(self, task: str, sid: str):
        self.active_tasks[sid] = task

    def record_score(self, task: str, score: float, passed: bool, sid: str):
        self.task_runs[task].append(score)
        self.task_total[task] += 1
        if passed:
            self.task_passed[task] += 1
            self.total_passed += 1
        self.total_runs += 1
        self.recent_scores.append(score)
        self.active_tasks.pop(sid, None)

    def record_mcp(self):
        self.mcp_calls += 1

    def record_error(self, sid: str):
        self.errors += 1
        self.active_tasks.pop(sid, None)

    def pass_rate(self) -> float:
        return (self.total_passed / self.total_runs * 100) if self.total_runs else 0.0

    def avg_score(self) -> float:
        return (sum(self.recent_scores) / len(self.recent_scores)) if self.recent_scores else 0.0

    def elapsed(self) -> str:
        secs = int(time.time() - self.start_time)
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def leaderboard(self, top_n: int = 15) -> list[tuple[str, int, int, float]]:
        """Return (task, n_total, n_pass, avg_score) sorted worst first."""
        rows = []
        for task in sorted(self.task_total.keys()):
            n = self.task_total[task]
            p = self.task_passed[task]
            avg = sum(self.task_runs[task]) / len(self.task_runs[task]) if self.task_runs[task] else 0.0
            rows.append((task, n, p, avg))
        rows.sort(key=lambda x: x[3])   # worst avg first
        return rows[:top_n]


# ── Rendering ─────────────────────────────────────────────────────────────────
def _bar(val: float, width: int = 12) -> str:
    filled = int(val / 100 * width)
    empty  = width - filled
    colour = GREEN if val >= 70 else (YELLOW if val >= 50 else RED)
    return clr("█" * filled, colour) + clr("░" * empty, DIM)


def render(activity: deque, stats: Stats, green_url: str):
    """Clear the screen and print the full dashboard."""
    # Move cursor to top-left (don't clear — less flicker)
    sys.stdout.write("\033[H\033[2J")

    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # ── Header ───────────────────────────────────────────────────────────────
    title = f"  AgentBench Live Dashboard  |  {now}  |  elapsed {stats.elapsed()}"
    print(clr("═" * HEADER_WIDTH, DIM))
    print(clr(f"{title:^{HEADER_WIDTH}}", BOLD, WHITE))
    print(clr("═" * HEADER_WIDTH, DIM))

    # ── Summary bar ──────────────────────────────────────────────────────────
    pr = stats.pass_rate()
    active_n = len(stats.active_tasks)
    active_list = ", ".join(
        f"{t}({s[:6]})" for s, t in list(stats.active_tasks.items())[:4]
    )
    active_str = f"  [{active_list}]" if active_list else ""
    pr_colour = GREEN if pr >= 70 else (YELLOW if pr >= 50 else RED)
    print(
        clr(f"  Runs: {stats.total_runs}", BOLD) +
        f"  Pass: {clr(f'{pr:.0f}%', BOLD, pr_colour)}" +
        f"  Avg score: {clr(f'{stats.avg_score():.1f}', BOLD)}" +
        f"  MCP calls: {clr(str(stats.mcp_calls), CYAN)}" +
        f"  Errors: {clr(str(stats.errors), RED) if stats.errors else clr('0', DIM)}" +
        f"  Active: {clr(str(active_n), YELLOW)}{active_str}"
    )
    print(clr("─" * HEADER_WIDTH, DIM))

    # ── Two-column layout: ACTIVITY (left) | LEADERBOARD (right) ────────────
    LEFT_W  = 62
    RIGHT_W = HEADER_WIDTH - LEFT_W - 3   # 35

    # Prepare leaderboard lines
    lb_rows  = stats.leaderboard(top_n=18)
    lb_lines = [clr(f"{'Task':<9} {'Runs':>4} {'Pass%':>6} {'Avg':>6}  {'Bar':<13} {'Status':>6}", BOLD)]
    for task, n, p, avg in lb_rows:
        pr_t    = p / n * 100 if n else 0
        status  = clr("SOLID", GREEN) if pr_t >= 80 else (clr("FLAKY", YELLOW) if pr_t >= 50 else clr("WEAK ", RED))
        lb_lines.append(
            f"{task:<9} {n:>4} {pr_t:>5.0f}% {avg:>6.1f}  {_bar(avg):<13} {status}"
        )

    # Prepare activity lines (newest at bottom)
    act_lines = list(activity)[-20:]   # show last 20

    # Print side by side
    max_rows = max(len(lb_lines), len(act_lines) + 2)
    act_header = clr(f"{'LIVE ACTIVITY FEED':<{LEFT_W}}", BOLD, WHITE)
    lb_header  = clr(f"LEADERBOARD", BOLD, WHITE)

    print(f"  {act_header}  │  {lb_header}")
    print(f"  {clr('─' * (LEFT_W-2), DIM)}  │  {clr('─' * (RIGHT_W-2), DIM)}")

    for i in range(max_rows):
        act_part = ""
        if i < len(act_lines):
            ev = act_lines[i]
            raw = f"  {clr(ev['ts'], DIM)}  {ev['display']}"
            # strip ANSI for width calc
            plain = re.sub(r'\033\[[0-9;]*m', '', raw)
            pad = max(0, LEFT_W - len(plain))
            act_part = raw + " " * pad
        else:
            act_part = " " * LEFT_W

        lb_part = ""
        if i < len(lb_lines):
            lb_part = lb_lines[i]

        print(f"  {act_part}  │  {lb_part}")

    print(clr("═" * HEADER_WIDTH, DIM))
    print(
        clr(f"  🟢 Green: {green_url}", GREEN) +
        clr(f"   🟣 Purple: https://purple.agentbench.usebrainos.com", MAGENTA) +
        clr("   [Ctrl+C to exit]", DIM)
    )
    sys.stdout.flush()


# ── Main loop ─────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="AgentBench live dashboard")
    parser.add_argument("--green-url",  default=DEFAULT_GREEN_URL)
    parser.add_argument("--poll-secs",  type=int, default=3)
    parser.add_argument("--region",     default=REGION)
    parser.add_argument("--lookback",   type=int, default=120,
                        help="Seconds of history to load on startup (default 120)")
    args = parser.parse_args()

    cw      = boto3.client("logs", region_name=args.region)
    stats   = Stats()
    activity: deque = deque(maxlen=ACTIVITY_QUEUE_SIZE)

    # Stream cursors: stream_name → last_event_ts_ms
    green_cursors:  dict[str, int] = {}
    purple_cursors: dict[str, int] = {}

    # Seed with recent history so dashboard isn't blank on startup
    since_ms = int((time.time() - args.lookback) * 1000)

    print(clr(f"Connecting to CloudWatch ({args.region})…", CYAN))
    print(clr(f"Green log group:  {GREEN_LOG_GROUP}", DIM))
    print(clr(f"Purple log group: {PURPLE_LOG_GROUP}", DIM))
    print(clr(f"Loading {args.lookback}s of history…", DIM))

    try:
        while True:
            new_events = []

            # ── Poll green streams ────────────────────────────────────────
            try:
                green_streams = _latest_streams(cw, GREEN_LOG_GROUP, n=3)
            except Exception as e:
                green_streams = []
                activity.appendleft({
                    "ts": datetime.now().strftime("%H:%M:%S"),
                    "display": clr(f"⚠ CloudWatch error (green): {e}", RED),
                })

            for stream in green_streams:
                cursor = green_cursors.get(stream, since_ms)
                events = _fetch_new_events(cw, GREEN_LOG_GROUP, stream, cursor)
                for ev in events:
                    ts_ms = ev["timestamp"]
                    msg   = ev.get("message", "").rstrip()
                    parsed = parse_green_event(msg, ts_ms)
                    if parsed:
                        new_events.append(parsed)
                        # Update stats
                        if parsed["kind"] == "task_start":
                            stats.record_start(parsed["task"], parsed["sid"])
                        elif parsed["kind"] == "score":
                            stats.record_score(parsed["task"], parsed["overall"], parsed["passed"], parsed["sid"])
                        elif parsed["kind"] == "mcp_call":
                            stats.record_mcp()
                        elif parsed["kind"] == "error":
                            stats.record_error(parsed["sid"])
                    if ts_ms > cursor:
                        green_cursors[stream] = ts_ms

            # ── Poll purple streams ───────────────────────────────────────
            try:
                purple_streams = _latest_streams(cw, PURPLE_LOG_GROUP, n=3)
            except Exception:
                purple_streams = []

            for stream in purple_streams:
                cursor = purple_cursors.get(stream, since_ms)
                events = _fetch_new_events(cw, PURPLE_LOG_GROUP, stream, cursor)
                for ev in events:
                    ts_ms = ev["timestamp"]
                    msg   = ev.get("message", "").rstrip()
                    parsed = parse_purple_event(msg, ts_ms)
                    if parsed:
                        new_events.append(parsed)
                    if ts_ms > cursor:
                        purple_cursors[stream] = ts_ms

            # ── Merge + sort new events chronologically ───────────────────
            new_events.sort(key=lambda e: e["ts_ms"])
            for ev in new_events:
                activity.append(ev)

            # After first pass, only look at genuinely new events
            since_ms = int(time.time() * 1000) - 100   # 100 ms overlap

            render(activity, stats, args.green_url)
            time.sleep(args.poll_secs)

    except KeyboardInterrupt:
        print(f"\n{clr('Dashboard stopped.', DIM)}")
        if stats.total_runs:
            print(
                f"\nSession summary: {stats.total_runs} runs, "
                f"{stats.total_passed} passed ({stats.pass_rate():.0f}%), "
                f"avg score {stats.avg_score():.1f}, "
                f"elapsed {stats.elapsed()}"
            )
        sys.exit(0)


if __name__ == "__main__":
    main()
