"""
Green Agent — AgentBeats-compatible entry point.

Accepts --host, --port, --card-url CLI args (required by AgentBeats platform).
Runs our FastAPI server with those settings.
"""
from __future__ import annotations
import argparse
import os
import uvicorn


def main():
    parser = argparse.ArgumentParser(
        description="AgentBench Green Agent — Business Process Improvement Benchmark"
    )
    parser.add_argument("--host", type=str, default="0.0.0.0",
                        help="Host to bind server on")
    parser.add_argument("--port", type=int, default=9009,
                        help="Port to bind server on")
    parser.add_argument("--card-url", type=str, default=None,
                        help="Public URL advertised in AgentCard (e.g. https://benchmark.usebrainos.com)")
    args = parser.parse_args()

    # Propagate card-url so server.py can use it in AGENT_CARD
    if args.card_url:
        os.environ["GREEN_AGENT_HOST_URL"] = args.card_url

    # Propagate port
    os.environ["PORT"] = str(args.port)

    print(f"[green-agent] Starting on {args.host}:{args.port}", flush=True)
    if args.card_url:
        print(f"[green-agent] Advertising card URL: {args.card_url}", flush=True)

    uvicorn.run(
        "src.server:app",
        host=args.host,
        port=args.port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
