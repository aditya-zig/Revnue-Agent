from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.integrations.razorpay import (  # noqa: E402
    DUMBBELL_AMOUNT_PAISE,
    DUMBBELL_CURRENCY,
    RazorpayProviderError,
    fetch_order_by_id,
    order_receipt_for_idempotency_key,
)
from scripts.genuine_testmode_prepare import (  # noqa: E402
    DEFAULT_RUNTIME_DIR,
    ensure_runtime_directory,
    ensure_webhook_secret,
    read_test_credentials,
    write_runtime_environment,
)
from scripts.genuine_testmode_provider_probe import (  # noqa: E402
    REPO_ROOT,
    migrate,
    request_json,
    sqlite_path,
    wait_for_health,
    wait_for_receipt_reconciliation,
)

DEFAULT_DATABASE_URL = "sqlite:///./genuine-demo.db"
DEFAULT_PORT = 8000


def validate_simulation(simulation: dict) -> None:
    expected = {
        "payments_total": 999,
        "successes": 749,
        "failures": 250,
        "findings": 37,
    }
    for key, value in expected.items():
        if simulation.get(key) != value:
            raise RuntimeError(f"simulation_{key}_invalid")


def prove_provider_order(*, base_url: str, key_id: str, key_secret: str) -> dict[str, object]:
    idempotency_key = f"genuine-session-order-{uuid4().hex}"
    local_order = request_json(
        f"{base_url}/api/v1/orders",
        method="POST",
        headers={"Idempotency-Key": idempotency_key},
        body={},
    )
    order_id = local_order.get("order_id")
    if not isinstance(order_id, str) or not order_id.startswith("order_"):
        raise RuntimeError("provider_order_id_invalid")
    if local_order.get("amount") != DUMBBELL_AMOUNT_PAISE:
        raise RuntimeError("provider_order_amount_invalid")
    if local_order.get("currency") != DUMBBELL_CURRENCY:
        raise RuntimeError("provider_order_currency_invalid")

    receipt = order_receipt_for_idempotency_key(idempotency_key)
    try:
        provider_order = fetch_order_by_id(key_id, key_secret, order_id)
    except RazorpayProviderError as error:
        raise RuntimeError(error.diagnostic) from error
    if not (
        provider_order.get("id") == order_id
        and provider_order.get("amount") == DUMBBELL_AMOUNT_PAISE
        and provider_order.get("currency") == DUMBBELL_CURRENCY
        and provider_order.get("receipt") == receipt
    ):
        raise RuntimeError("provider_order_direct_fetch_mismatch")

    reconciled, attempts = wait_for_receipt_reconciliation(
        key_id, key_secret, receipt, order_id
    )
    return {
        "test_mode_order_created": True,
        "direct_fetch_verified": True,
        "provider_round_trip": True,
        "receipt_reconciliation_verified": reconciled is not None,
        "receipt_reconciliation_attempts": attempts,
        "amount": DUMBBELL_AMOUNT_PAISE,
        "currency": DUMBBELL_CURRENCY,
    }


def start_session(
    *,
    credentials_file: Path,
    runtime_dir: Path = DEFAULT_RUNTIME_DIR,
    database_url: str = DEFAULT_DATABASE_URL,
    port: int = DEFAULT_PORT,
    reset_db: bool = True,
) -> dict[str, object]:
    runtime_dir = ensure_runtime_directory(runtime_dir)
    key_id, key_secret = read_test_credentials(credentials_file)
    webhook_secret_path, webhook_secret, generated = ensure_webhook_secret(runtime_dir)
    write_runtime_environment(
        runtime_dir,
        key_id=key_id,
        key_secret=key_secret,
        webhook_secret=webhook_secret,
        database_url=database_url,
    )
    db_path = sqlite_path(database_url)
    if reset_db and db_path is not None and db_path.exists():
        db_path.unlink()
    migrate(database_url)

    environment = os.environ.copy()
    environment.update(
        {
            "REROUTE_DATABASE_URL": database_url,
            "REROUTE_RAZORPAY_KEY_ID": key_id,
            "REROUTE_RAZORPAY_KEY_SECRET": key_secret,
            "REROUTE_RAZORPAY_WEBHOOK_SECRET": webhook_secret,
        }
    )
    base_url = f"http://127.0.0.1:{port}"
    log_path = runtime_dir / "genuine-demo-server.log"
    pid_path = runtime_dir / "genuine-demo.pid"
    if pid_path.exists():
        raise RuntimeError("existing_genuine_demo_pid")

    with log_path.open("ab") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=REPO_ROOT,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )

    successful = False
    try:
        pid_path.write_text(f"{process.pid}\n", encoding="utf-8")
        wait_for_health(base_url)
        simulation = request_json(
            f"{base_url}/api/v1/data/simulate-999", method="POST"
        )
        validate_simulation(simulation)
        provider = prove_provider_order(
            base_url=base_url, key_id=key_id, key_secret=key_secret
        )
        report = {
            "ready": True,
            "key_mode": "TEST",
            "server": {"running": True, "base_url": base_url, "pid_file": str(pid_path)},
            "runtime": {
                "directory": str(runtime_dir),
                "webhook_secret_present": True,
                "webhook_secret_generated": generated,
                "webhook_secret_file": str(webhook_secret_path),
                "database_url": database_url,
            },
            "historical_population": {
                "payments": 999,
                "captured": 749,
                "failed": 250,
                "findings": 37,
            },
            "provider_order": provider,
            "next_step": "establish_public_https",
        }
        (runtime_dir / "genuine-session.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        successful = True
        return report
    finally:
        if not successful:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            pid_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Start one reproducible local Razorpay Test Mode proof session."
    )
    parser.add_argument("--credentials-file", required=True, type=Path)
    parser.add_argument("--runtime-dir", type=Path, default=DEFAULT_RUNTIME_DIR)
    parser.add_argument("--database-url", default=DEFAULT_DATABASE_URL)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-reset-db", action="store_true")
    args = parser.parse_args()
    try:
        report = start_session(
            credentials_file=args.credentials_file,
            runtime_dir=args.runtime_dir,
            database_url=args.database_url,
            port=args.port,
            reset_db=not args.no_reset_db,
        )
    except (RuntimeError, ValueError) as error:
        print(json.dumps({"ready": False, "error": str(error)}, indent=2))
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
