#!/usr/bin/env python3
"""AgentGrid cross-platform task runner.

Works the same in macOS/Linux terminals and Windows PowerShell:

    python scripts/dev.py setup          # create .venv (Python 3.12), install deps, create .env
    python scripts/dev.py migrate        # apply database migrations
    python scripts/dev.py seed           # seed super admin (+ demo tenants)
    python scripts/dev.py run            # start the API with auto-reload on http://localhost:8000
    python scripts/dev.py test [-- -k auth]
    python scripts/dev.py lint
    python scripts/dev.py makemigration "add foo table"
    python scripts/dev.py docker-up [--observability] [--worker]
    python scripts/dev.py docker-down

Only the standard library is used here, so it runs before the venv exists.
"""

from __future__ import annotations

import argparse
import os
import platform
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"
IS_WINDOWS = platform.system() == "Windows"
REQUIRED = (3, 12)


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def info(msg: str) -> None:
    print(f"==> {msg}", flush=True)


def fail(msg: str, code: int = 1) -> None:
    print(f"ERROR: {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


def run(cmd: list[str | Path], *, check: bool = True, env: dict[str, str] | None = None) -> int:
    printable = " ".join(str(c) for c in cmd)
    info(printable)
    merged = {**os.environ, "PYTHONUTF8": "1", **(env or {})}
    try:
        result = subprocess.run([str(c) for c in cmd], cwd=ROOT, env=merged, check=False)
    except FileNotFoundError:
        fail(f"command not found: {cmd[0]}")
    except KeyboardInterrupt:
        return 130
    if check and result.returncode != 0:
        fail(f"command failed ({result.returncode}): {printable}", result.returncode)
    return result.returncode


def require_venv() -> Path:
    py = venv_python()
    if not py.exists():
        fail("virtualenv not found. Run:  python scripts/dev.py setup")
    return py


# ------------------------------------------------------------------ python discovery
def _version_of(cmd: list[str]) -> tuple[int, int] | None:
    try:
        out = subprocess.run(
            [*cmd, "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if out.returncode != 0:
        return None
    major, minor = out.stdout.split()
    return int(major), int(minor)


def find_python(explicit: str | None) -> list[str]:
    if explicit:
        return [explicit]
    if sys.version_info[:2] == REQUIRED:
        return [sys.executable]
    candidates: list[list[str]] = []
    if IS_WINDOWS:
        candidates.append(["py", "-3.12"])
    candidates.append(["python3.12"])
    if shutil.which("uv"):
        found = subprocess.run(["uv", "python", "find", "3.12"], capture_output=True, text=True, check=False)
        if found.returncode == 0 and found.stdout.strip():
            candidates.append([found.stdout.strip()])
    for cmd in candidates:
        if _version_of(cmd) == REQUIRED:
            return cmd
    if sys.version_info[:2] > REQUIRED:
        info(f"Python 3.12 not found; using {platform.python_version()} (3.12 is the supported version)")
        return [sys.executable]
    fail(
        "Python 3.12 is required.\n"
        "  macOS:   brew install python@3.12   (or: uv python install 3.12)\n"
        "  Windows: winget install Python.Python.3.12   (then reopen PowerShell)\n"
        "Or pass an interpreter explicitly: python scripts/dev.py setup --python <path>"
    )
    raise AssertionError  # unreachable


# ------------------------------------------------------------------ commands
def cmd_setup(args: argparse.Namespace) -> None:
    if not venv_python().exists():
        py = find_python(args.python)
        info(f"Creating virtualenv in {VENV.name} with {' '.join(py)}")
        run([*py, "-m", "venv", str(VENV)])
    vpy = venv_python()
    reqs = ROOT / ("requirements.txt" if args.prod else "requirements-dev.txt")
    if shutil.which("uv") and not args.no_uv:
        run(["uv", "pip", "install", "--python", vpy, "-r", reqs])
    else:
        run([vpy, "-m", "pip", "install", "--upgrade", "pip"])
        run([vpy, "-m", "pip", "install", "-r", reqs])

    env_file, example = ROOT / ".env", ROOT / ".env.example"
    if not env_file.exists():
        text = example.read_text(encoding="utf-8")
        text = text.replace(
            "JWT_SECRET=change-me-to-a-long-random-string-at-least-32-chars",
            f"JWT_SECRET={secrets.token_urlsafe(48)}",
        )
        env_file.write_text(text, encoding="utf-8")
        info("Created .env from .env.example (with a random JWT_SECRET). Edit DATABASE_URL / REDIS_URL next.")
    else:
        info(".env already exists - left untouched")
    (ROOT / "data").mkdir(exist_ok=True)
    # One command per line: Windows PowerShell 5.1 has no `&&`.
    info("Setup complete. Next steps:")
    for step in ("migrate", "seed", "run"):
        print(f"    python scripts/dev.py {step}")


def cmd_migrate(_args: argparse.Namespace) -> None:
    run([require_venv(), "-m", "alembic", "upgrade", "head"])


def cmd_makemigration(args: argparse.Namespace) -> None:
    run([require_venv(), "-m", "alembic", "revision", "--autogenerate", "-m", args.message])


def cmd_seed(_args: argparse.Namespace) -> None:
    run([require_venv(), "-m", "app.cli", "seed"])


def cmd_run(args: argparse.Namespace) -> None:
    cmd: list[str | Path] = [require_venv(), "-m", "app.server", "--port", str(args.port)]
    if not args.no_reload:
        cmd.append("--reload")
    run(cmd, check=False)


def cmd_test(args: argparse.Namespace) -> None:
    run([require_venv(), "-m", "pytest", *args.pytest_args])


def cmd_lint(_args: argparse.Namespace) -> None:
    vpy = require_venv()
    run([vpy, "-m", "ruff", "check", "."])
    run([vpy, "-m", "ruff", "format", "--check", "."])


def _docker() -> list[str]:
    if not shutil.which("docker"):
        fail(
            "Docker is not installed. Install Docker Desktop (macOS/Windows) or use lite mode "
            "(see README: Quick start)."
        )
    return ["docker", "compose"]


def cmd_docker_up(args: argparse.Namespace) -> None:
    cmd = _docker()
    if args.observability:
        cmd += ["--profile", "observability"]
    if args.worker:
        cmd += ["--profile", "worker"]
    run([*cmd, "up", "-d", "--build"])
    info("Stack starting. API: http://localhost:8000  (docs: /docs)  -  logs: docker compose logs -f api")


def cmd_docker_down(_args: argparse.Namespace) -> None:
    run([*_docker(), "--profile", "observability", "--profile", "worker", "down"])


def main() -> None:
    parser = argparse.ArgumentParser(description="AgentGrid task runner (macOS / Windows / Linux)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("setup", help="create venv, install dependencies, create .env")
    p.add_argument("--python", help="path to a Python 3.12 interpreter")
    p.add_argument("--prod", action="store_true", help="install runtime dependencies only")
    p.add_argument("--no-uv", action="store_true", help="use pip even if uv is installed")
    p.set_defaults(func=cmd_setup)

    sub.add_parser("migrate", help="apply database migrations").set_defaults(func=cmd_migrate)

    p = sub.add_parser("makemigration", help="autogenerate a migration")
    p.add_argument("message")
    p.set_defaults(func=cmd_makemigration)

    sub.add_parser("seed", help="seed super admin and demo data").set_defaults(func=cmd_seed)

    p = sub.add_parser("run", help="run the API server (auto-reload)")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-reload", action="store_true")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("test", help="run pytest (extra args after --)")
    p.add_argument("pytest_args", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_test)

    sub.add_parser("lint", help="ruff lint + format check").set_defaults(func=cmd_lint)

    p = sub.add_parser("docker-up", help="start the full stack with Docker Compose")
    p.add_argument("--observability", action="store_true", help="also start Prometheus + Grafana")
    p.add_argument("--worker", action="store_true", help="also start a separate background worker")
    p.set_defaults(func=cmd_docker_up)

    sub.add_parser("docker-down", help="stop the Docker Compose stack").set_defaults(func=cmd_docker_down)

    args = parser.parse_args()
    if getattr(args, "pytest_args", None) and args.pytest_args[0] == "--":
        args.pytest_args = args.pytest_args[1:]
    args.func(args)


if __name__ == "__main__":
    main()
