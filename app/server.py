"""Process entrypoint: `python -m app.server [--reload] [--migrate]`.

Used identically by scripts/dev.py (macOS / Windows / Linux) and the Docker
image, so there are no shell-specific start scripts.
"""

from __future__ import annotations

import argparse
import os

import uvicorn

from app.core.config import settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the AgentGrid API server")
    parser.add_argument("--reload", action="store_true", help="auto-reload on code changes (development)")
    parser.add_argument("--migrate", action="store_true", help="run `alembic upgrade head` before starting")
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", settings.port)))
    args = parser.parse_args()

    if args.migrate or os.environ.get("RUN_MIGRATIONS", "").lower() in {"1", "true", "yes"}:
        from app.cli import run_migrations

        run_migrations()

    workers = 1 if args.reload else settings.web_concurrency
    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        reload_dirs=["app"] if args.reload else None,
        workers=workers,
        proxy_headers=True,
        forwarded_allow_ips="*",
        log_config=None,  # our JSON logging is configured in app.main
        access_log=False,
        timeout_graceful_shutdown=20,
    )


if __name__ == "__main__":
    main()
