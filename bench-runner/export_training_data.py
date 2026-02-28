#!/usr/bin/env python3
"""Export training data from AgentBench to S3 for BrainOS fine-tuning."""
import argparse, httpx, json

GREEN_URL = "https://benchmark.usebrainos.com"

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=float, default=4)
    args = parser.parse_args()
    print(f"Exporting training data from last {args.hours}h...")
    resp = httpx.post(f"{GREEN_URL}/training-data/export", params={"hours": args.hours}, timeout=60)
    data = resp.json()
    print(json.dumps(data, indent=2))

if __name__ == "__main__":
    main()
