from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.genuine_testmode_provider_probe import request_json
from scripts.genuine_testmode_session import start_session

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = REPO_ROOT / ".reroute-local"
CONFIG_PATH = RUNTIME_DIR / "demo-config.json"
PID_PATH = RUNTIME_DIR / "genuine-demo.pid"
SESSION_PATH = RUNTIME_DIR / "genuine-session.json"
LOG_PATH = RUNTIME_DIR / "genuine-demo-server.log"

DEFAULT_CONFIG = {
    "host": "127.0.0.1",
    "port": 8000,
    "database_url": "sqlite:///./genuine-demo.db",
}


def load_demo_config(credentials_file: str | Path | None = None) -> dict[str, Any]:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    if credentials_file is not None:
        path = Path(credentials_file).expanduser().resolve()
        if not path.is_file():
            raise RuntimeError("demo_credentials_file_not_found")
        current = _read_config()
        config = {**DEFAULT_CONFIG, **current, "credentials_file": str(path)}
        CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        return config
    config = _read_config()
    if not config.get("credentials_file"):
        raise RuntimeError("demo_credentials_not_configured")
    return {**DEFAULT_CONFIG, **config}


def _read_config() -> dict[str, Any]:
    if not CONFIG_PATH.is_file():
        return {}
    try:
        value = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("demo_config_invalid") from error
    if not isinstance(value, dict):
        raise RuntimeError("demo_config_invalid")
    return value


def read_demo_pid() -> int | None:
    try:
        value = PID_PATH.read_text(encoding="utf-8").strip()
        return int(value) if value.isdecimal() and int(value) > 0 else None
    except (OSError, ValueError):
        return None


def process_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _is_reroute_process(pid: int) -> bool:
    try:
        command = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()
    except (OSError, UnicodeDecodeError):
        return False
    return "python" in command and "uvicorn" in command and "app.main:app" in command


def _urls(config: dict[str, Any]) -> dict[str, str]:
    base = f"http://{config['host']}:{config['port']}"
    return {
        "dashboard_url": f"{base}/",
        "storefront_url": f"{base}/storefront",
        "health_url": f"{base}/health",
    }


def demo_status(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or load_demo_config()
    urls = _urls(config)
    pid = read_demo_pid()
    running = pid is not None and process_is_running(pid) and _is_reroute_process(pid)
    health = False
    population: dict[str, Any] = {}
    if running:
        try:
            health = request_json(urls["health_url"]).get("status") == "ok"
            dashboard = request_json(f"{urls['dashboard_url']}api/v1/dashboard")
            population = dashboard.get("population", {})
        except RuntimeError:
            health = False
    return {
        "running": running,
        "pid": pid if running else None,
        "health": health,
        **urls,
        "database": Path(str(config["database_url"]).removeprefix("sqlite:///")).name,
        "session_ready": SESSION_PATH.is_file(),
        "population": {
            "total": population.get("total"),
            "captured": population.get("captured"),
            "failed": population.get("failed"),
        },
    }


def start_demo(
    credentials_file: str | Path | None = None,
    *,
    reset_db: bool = True,
) -> dict[str, Any]:
    config = load_demo_config(credentials_file)
    current = demo_status(config)
    if current["running"] and current["health"]:
        return {"result": "already_running", **current}
    if PID_PATH.exists():
        PID_PATH.unlink()
    report = start_session(
        credentials_file=Path(str(config["credentials_file"])),
        runtime_dir=RUNTIME_DIR,
        database_url=str(config["database_url"]),
        port=int(config["port"]),
        reset_db=reset_db,
    )
    return {"result": "started", **report}


def stop_demo() -> dict[str, Any]:
    pid = read_demo_pid()
    if pid is None:
        if PID_PATH.exists():
            PID_PATH.unlink()
            return {"result": "stale_pid_cleaned"}
        return {"result": "already_stopped"}
    if not process_is_running(pid):
        PID_PATH.unlink(missing_ok=True)
        return {"result": "stale_pid_cleaned"}
    if not _is_reroute_process(pid):
        return {"result": "error", "error": "pid_not_reroute_process", "pid": pid}
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and process_is_running(pid):
        time.sleep(0.1)
    if process_is_running(pid):
        os.kill(pid, signal.SIGKILL)
    PID_PATH.unlink(missing_ok=True)
    return {"result": "stopped", "pid": pid}


def restart_demo(
    credentials_file: str | Path | None = None,
    *,
    reset_db: bool = True,
) -> dict[str, Any]:
    stop_demo()
    return start_demo(credentials_file, reset_db=reset_db)


def open_demo() -> dict[str, Any]:
    status = demo_status()
    if not status["running"] or not status["health"]:
        return {"result": "demo_not_running"}
    for key in ("dashboard_url", "storefront_url"):
        subprocess.Popen(["xdg-open", status[key]], start_new_session=True)
    return {"result": "opened", "urls": [status["dashboard_url"], status["storefront_url"]]}


def logs_demo(follow: bool = False) -> dict[str, Any]:
    if not LOG_PATH.is_file():
        return {"result": "demo_log_not_found"}
    if follow:
        subprocess.run(["tail", "-f", str(LOG_PATH)], check=False)
        return {"result": "following"}
    lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()[-80:]
    return {"result": "logs", "lines": lines}


def public_status() -> dict[str, Any]:
    path = RUNTIME_DIR / "public-url"
    if not path.is_file():
        return {"configured": False, "public_url": None, "health": False}
    url = path.read_text(encoding="utf-8").strip()
    try:
        healthy = request_json(f"{url}/health").get("status") == "ok"
    except RuntimeError:
        healthy = False
    return {"configured": bool(url), "public_url": url or None, "health": healthy}


def main() -> int:
    parser = argparse.ArgumentParser(description="Control the local ReRoute demo runtime.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("start", "restart"):
        command = subparsers.add_parser(name)
        command.add_argument("--credentials-file", type=Path)
        command.add_argument("--keep-db", action="store_true")
    subparsers.add_parser("status")
    subparsers.add_parser("stop")
    subparsers.add_parser("open")
    logs = subparsers.add_parser("logs")
    logs.add_argument("--follow", action="store_true")
    subparsers.add_parser("public-status")
    args = parser.parse_args()
    try:
        if args.command == "start":
            result = start_demo(args.credentials_file, reset_db=not args.keep_db)
        elif args.command == "restart":
            result = restart_demo(args.credentials_file, reset_db=not args.keep_db)
        elif args.command == "status":
            result = demo_status()
        elif args.command == "stop":
            result = stop_demo()
        elif args.command == "open":
            result = open_demo()
        elif args.command == "logs":
            result = logs_demo(args.follow)
        else:
            result = public_status()
    except (RuntimeError, ValueError) as error:
        result = {"result": "error", "error": str(error)}
        print(json.dumps(result, indent=2))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
