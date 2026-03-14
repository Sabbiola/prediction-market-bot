from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from prediction_market_bot.ui.app import create_web_app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prediction-market-bot-ui",
        description="Web control-plane server for prediction-market-bot.",
    )
    parser.add_argument("--config", type=Path, default=Path("config/app.yaml"), help="Path to app config YAML.")
    parser.add_argument(
        "--agents-config",
        type=Path,
        default=Path("config/agents.yaml"),
        help="Path to agents config YAML.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host.")
    parser.add_argument("--port", type=int, default=8080, help="Bind port.")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for local development.")
    parser.add_argument("--log-level", default="info", help="Uvicorn log level.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    app = create_web_app(config_path=args.config, agents_config_path=args.agents_config)

    import uvicorn

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        reload=bool(args.reload),
        log_level=str(args.log_level).lower(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
