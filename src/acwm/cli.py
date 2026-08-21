"""ACWM single-process command line entry point."""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from acwm.adapters import HermesACPTransportAdapter
from acwm.api import AppSettings, create_app
from acwm.config import load_capabilities, load_journeys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="acwm")
    subcommands = parser.add_subparsers(dest="command", required=True)
    serve = subcommands.add_parser("serve", help="Run the single-worker ACWM control plane")
    serve.add_argument("--data-dir", type=Path, default=Path(".acwm-data"))
    serve.add_argument("--capabilities", type=Path, default=Path("config/capabilities.yaml"))
    serve.add_argument("--journeys", type=Path, default=Path("config/journeys.yaml"))
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8787)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command != "serve":
        raise SystemExit(2)
    capabilities = load_capabilities(args.capabilities)
    journeys = load_journeys(args.journeys)
    if len(capabilities) != 1:
        raise SystemExit("ACWM v1 requires exactly one Capability profile")
    descriptor = next(iter(capabilities.values()))
    transport = HermesACPTransportAdapter(descriptor)
    app = create_app(
        AppSettings(
            data_dir=args.data_dir,
            host=args.host,
            api_key=os.environ.get("ACWM_API_KEY"),
        ),
        transport=transport,
        capabilities=capabilities,
        journey_definitions=journeys,
    )
    uvicorn.run(app, host=args.host, port=args.port, workers=1)


if __name__ == "__main__":
    main()
