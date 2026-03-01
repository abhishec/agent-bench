#!/usr/bin/env python3
"""
Convert JSONL stress-test results to BrainOS RL fine-tuning format.

Usage:
    python3 bench-runner/convert_to_training.py <results.jsonl> <training.jsonl>
"""
import json
import sys
from pathlib import Path

def main():
    if len(sys.argv) < 3:
        print("Usage: convert_to_training.py <results_file> <training_file>")
        sys.exit(1)

    results_file = sys.argv[1]
    training_file = sys.argv[2]

    rows = [json.loads(l) for l in open(results_file) if l.strip()]
    ts = Path(results_file).stem   # use filename as source tag

    training = []
    for r in rows:
        example_type = "positive" if r.get("overall", 0) >= 70 else "negative"
        training.append({
            "task_id":      r["task_id"],
            "difficulty":   r.get("difficulty", "none"),
            "score":        r["overall"],
            "tool_calls":   r.get("tool_calls", 0),
            "answer":       r.get("answer", ""),
            "example_type": example_type,
            "source":       f"stress_test_{ts}",
        })

    pos = sum(1 for t in training if t["example_type"] == "positive")
    neg = len(training) - pos
    print(f"Training examples: {pos} positive, {neg} negative, {len(training)} total")

    with open(training_file, "w") as f:
        for t in training:
            f.write(json.dumps(t) + "\n")

    print(f"Written to {training_file}")

if __name__ == "__main__":
    main()
