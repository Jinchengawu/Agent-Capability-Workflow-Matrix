"""ACWM single-process command line entry point."""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import uvicorn

from acwm.adapters import HermesACPCapabilityAdapter, HttpSyncCapabilityAdapter
from acwm.api import AppSettings, create_app
from acwm.application.runtime import DefaultCapabilityRuntime
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
    adapters: dict[str, Any] = {}
    for capability_id, spec in capabilities.adapter_configs.items():
        if spec.type == "hermes.acp":
            adapters[capability_id] = HermesACPCapabilityAdapter(
                spec.config, capabilities.descriptors[capability_id].policy
            )
        else:
            adapters[capability_id] = HttpSyncCapabilityAdapter(spec.config)
    runtime = DefaultCapabilityRuntime(catalog=capabilities, adapters=adapters, event_sink=None)
    app = create_app(
        AppSettings(
            data_dir=args.data_dir,
            host=args.host,
            api_key=os.environ.get("ACWM_API_KEY"),
        ),
        runtime=runtime,
        catalog=capabilities,
        journey_definitions=journeys,
    )
    uvicorn.run(app, host=args.host, port=args.port, workers=1)


if __name__ == "__main__":
    main()
