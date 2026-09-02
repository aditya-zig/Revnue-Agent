from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.demo_runtime import RUNTIME_DIR, demo_status, start_demo  # noqa: E402
from scripts.genuine_testmode_provider_probe import request_json  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_URL_PATH = RUNTIME_DIR / "public-url"
ZROK_PID_PATH = RUNTIME_DIR / "zrok.pid"
ZROK_LOG_PATH = RUNTIME_DIR / "zrok.log"
WEBHOOK_SECRET_PATH = RUNTIME_DIR / "webhook-secret"
WEBHOOK_EVENTS = ("payment.failed", "payment.captured")
TEST_MODE_WEBHOOK_OTP = "754081"
URL_RE = re.compile(r"https://[A-Za-z0-9][A-Za-z0-9._:-]*(?:/[^\s\"']*)?")


def _zrok_binary() -> str:
    local = Path.home() / ".local" / "bin" / "zrok2"
    if local.is_file():
        return str(local)
    found = shutil.which("zrok2")
    if found:
        return found
    raise RuntimeError("zrok_not_installed")


def _read_pid(path: Path) -> int | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return int(value) if value.isdecimal() and int(value) > 0 else None


def _running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _is_zrok_share(pid: int) -> bool:
    try:
        command = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()
    except (OSError, UnicodeDecodeError):
        return False
    return "zrok2" in command and "share" in command and "public" in command


def extract_public_url(text: str) -> str | None:
    endpoints = re.search(r'frontendEndpoints?:\s*"(https://[^"\s]+)"', text)
    if endpoints:
        return endpoints.group(1).rstrip("/")
    matches = URL_RE.findall(text)
    return matches[-1].rstrip("/") if matches else None


def _zrok_enabled(binary: str) -> bool:
    result = subprocess.run(
        [binary, "status"],
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
        check=False,
    )
    return result.returncode == 0


def _wait_public_url(process: subprocess.Popen[bytes], timeout: float = 20.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("zrok_share_exited")
        try:
            output = ZROK_LOG_PATH.read_text(encoding="utf-8", errors="replace")
        except OSError:
            output = ""
        url = extract_public_url(output)
        if url:
            return url
        time.sleep(0.2)
    raise RuntimeError("zrok_public_url_not_ready")


def _wait_health(public_url: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if request_json(f"{public_url}/health").get("status") == "ok":
                return
        except RuntimeError:
            pass
        time.sleep(0.5)
    raise RuntimeError("public_health_not_ready")


def invalid_signature_rejected(public_url: str) -> bool:
    request = urllib.request.Request(
        f"{public_url}/api/v1/webhooks/razorpay",
        data=b"{}",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": "invalid",
        },
    )
    try:
        urllib.request.urlopen(request, timeout=10)
    except urllib.error.HTTPError as error:
        return error.code == 401
    except Exception as error:
        raise RuntimeError(f"public_webhook_probe={type(error).__name__}") from error
    return False


def webhook_configuration(public_url: str) -> dict[str, object]:
    return {
        "mode": "TEST",
        "url": f"{public_url}/api/v1/webhooks/razorpay",
        "events": list(WEBHOOK_EVENTS),
        "secret_file": str(WEBHOOK_SECRET_PATH),
        "test_mode_otp": TEST_MODE_WEBHOOK_OTP,
        "dashboard_action_required": True,
    }


def _provider_evidence() -> dict[str, object]:
    try:
        dashboard = request_json("http://127.0.0.1:8000/api/v1/dashboard")
    except RuntimeError:
        return {"present": False, "signed_event_count": 0}
    provider = dashboard.get("provider_evidence") or {}
    return {
        "present": bool(provider.get("present")),
        "signed_event_count": provider.get("signed_event_count", 0),
        "payment_failed_present": bool(provider.get("payment_failed_present")),
        "payment_captured_present": bool(provider.get("payment_captured_present")),
        "raw_body_present": bool(provider.get("raw_body_present")),
        "checkout_order_owned": bool(provider.get("checkout_order_owned")),
        "provider_delivery_claim": provider.get("provider_delivery_claim"),
    }


def start() -> dict[str, Any]:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    demo = demo_status()
    if not demo.get("running") or not demo.get("health"):
        start_demo()
        demo = demo_status()
    if not demo.get("running") or not demo.get("health"):
        raise RuntimeError("demo_not_running")

    existing = _read_pid(ZROK_PID_PATH)
    if existing and _running(existing):
        if not _is_zrok_share(existing):
            raise RuntimeError("zrok_pid_not_share_process")
        if not PUBLIC_URL_PATH.is_file():
            raise RuntimeError("zrok_running_without_public_url")
        public_url = PUBLIC_URL_PATH.read_text(encoding="utf-8").strip().rstrip("/")
        _wait_health(public_url)
        return {"result": "already_running", **status()}
    ZROK_PID_PATH.unlink(missing_ok=True)

    binary = _zrok_binary()
    if not _zrok_enabled(binary):
        raise RuntimeError("zrok_not_enabled")

    with ZROK_LOG_PATH.open("wb") as log:
        process = subprocess.Popen(
            [binary, "share", "public", "--headless", "http://127.0.0.1:8000"],
            cwd=REPO_ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    ZROK_PID_PATH.write_text(f"{process.pid}\n", encoding="utf-8")
    try:
        public_url = _wait_public_url(process)
        PUBLIC_URL_PATH.write_text(public_url + "\n", encoding="utf-8")
        _wait_health(public_url)
        if not invalid_signature_rejected(public_url):
            raise RuntimeError("public_webhook_did_not_fail_closed")
        return {
            "result": "started",
            "ready": True,
            "public_url": public_url,
            "public_health": True,
            "invalid_signature_rejected": True,
            "webhook_configuration": webhook_configuration(public_url),
            "provider_evidence": _provider_evidence(),
            "next_step": "configure_razorpay_test_mode_webhook",
        }
    except Exception:
        if process.poll() is None:
            process.terminate()
        ZROK_PID_PATH.unlink(missing_ok=True)
        PUBLIC_URL_PATH.unlink(missing_ok=True)
        raise


def status() -> dict[str, Any]:
    pid = _read_pid(ZROK_PID_PATH)
    running = bool(pid and _running(pid) and _is_zrok_share(pid))
    public_url = (
        PUBLIC_URL_PATH.read_text(encoding="utf-8").strip().rstrip("/")
        if PUBLIC_URL_PATH.is_file()
        else ""
    )
    healthy = False
    if public_url:
        try:
            healthy = request_json(f"{public_url}/health").get("status") == "ok"
        except RuntimeError:
            pass
    return {
        "running": running,
        "pid": pid if running else None,
        "public_url": public_url or None,
        "public_health": healthy,
        "webhook_configuration": webhook_configuration(public_url) if public_url else None,
        "provider_evidence": _provider_evidence(),
    }


def stop() -> dict[str, object]:
    pid = _read_pid(ZROK_PID_PATH)
    if pid is None:
        ZROK_PID_PATH.unlink(missing_ok=True)
        return {"result": "already_stopped"}
    if not _running(pid):
        ZROK_PID_PATH.unlink(missing_ok=True)
        return {"result": "stale_pid_cleaned"}
    if not _is_zrok_share(pid):
        return {"result": "error", "error": "zrok_pid_not_share_process", "pid": pid}
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and _running(pid):
        time.sleep(0.1)
    if _running(pid):
        os.kill(pid, signal.SIGKILL)
    ZROK_PID_PATH.unlink(missing_ok=True)
    return {"result": "stopped", "pid": pid}


def main() -> int:
    parser = argparse.ArgumentParser(description="Control the genuine Razorpay webhook tunnel.")
    parser.add_argument("command", choices=("start", "status", "stop"))
    args = parser.parse_args()
    try:
        if args.command == "start":
            result = start()
        elif args.command == "status":
            result = status()
        else:
            result = stop()
    except (RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({"result": "error", "error": str(error)}, indent=2))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
