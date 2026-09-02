from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.integrations.razorpay import (  # noqa: E402
    DUMBBELL_AMOUNT_PAISE,
    DUMBBELL_CURRENCY,
    RazorpayProviderError,
    fetch_order_by_id,
    find_order_by_receipt,
    order_receipt_for_idempotency_key,
)
from scripts.genuine_testmode_prepare import (  # noqa: E402
    DEFAULT_RUNTIME_DIR,
    ensure_runtime_directory,
    ensure_webhook_secret,
    read_test_credentials,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
RECEIPT_RETRY_DELAYS_SECONDS = (0.25, 0.5, 1.0, 2.0, 3.0)


def request_json(url: str, *, method: str = "GET", headers: dict[str, str] | None = None,
                 body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"app_http_status={error.code}") from error
    except Exception as error:
        raise RuntimeError(f"app_request={type(error).__name__}") from error
    if not isinstance(payload, dict):
        raise RuntimeError("app_response_invalid")
    return payload


def wait_for_health(base_url: str, timeout_seconds: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            if request_json(f"{base_url}/health").get("status") == "ok":
                return
        except RuntimeError:
            pass
        time.sleep(0.2)
    raise RuntimeError("local_server_not_ready")


def migrate(database_url: str) -> None:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


def wait_for_receipt_reconciliation(
    key_id: str, key_secret: str, receipt: str, expected_order_id: str
) -> tuple[dict | None, int]:
    attempts = 0
    for index in range(len(RECEIPT_RETRY_DELAYS_SECONDS) + 1):
        attempts += 1
        order = find_order_by_receipt(key_id, key_secret, receipt)
        if order is not None:
            if order.get("id") != expected_order_id:
                raise RuntimeError("provider_receipt_resolved_to_different_order")
            return order, attempts
        if index < len(RECEIPT_RETRY_DELAYS_SECONDS):
            time.sleep(RECEIPT_RETRY_DELAYS_SECONDS[index])
    return None, attempts


def sqlite_path(database_url: str) -> Path | None:
    prefix = "sqlite:///"
    return Path(database_url[len(prefix):]) if database_url.startswith(prefix) else None


def run_probe(*, credentials_file: Path, runtime_dir: Path = DEFAULT_RUNTIME_DIR,
              database_url: str = "sqlite:///./genuine-testmode.db", port: int = 8011,
              reset_db: bool = False) -> dict[str, object]:
    ensure_runtime_directory(runtime_dir)
    key_id, key_secret = read_test_credentials(credentials_file)
    _, webhook_secret, generated = ensure_webhook_secret(runtime_dir)
    db_path = sqlite_path(database_url)
    if reset_db and db_path and db_path.exists():
        db_path.unlink()
    migrate(database_url)
    environment = os.environ.copy()
    environment.update({
        "REROUTE_DATABASE_URL": database_url,
        "REROUTE_RAZORPAY_KEY_ID": key_id,
        "REROUTE_RAZORPAY_KEY_SECRET": key_secret,
        "REROUTE_RAZORPAY_WEBHOOK_SECRET": webhook_secret,
    })
    base_url = f"http://127.0.0.1:{port}"
    log_path = runtime_dir / "provider-probe-server.log"
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
             "--port", str(port), "--log-level", "warning"],
            env=environment, stdout=log, stderr=subprocess.STDOUT,
        )
    try:
        wait_for_health(base_url)
        simulation = request_json(f"{base_url}/api/v1/data/simulate-999", method="POST")
        if simulation.get("payments_total") != 999:
            raise RuntimeError("simulation_count_invalid")
        idempotency_key = f"genuine-provider-probe-{uuid4().hex}"
        order = request_json(
            f"{base_url}/api/v1/orders", method="POST",
            headers={"Idempotency-Key": idempotency_key}, body={},
        )
        order_id = order.get("order_id")
        if not isinstance(order_id, str) or not order_id.startswith("order_"):
            raise RuntimeError("storefront_provider_order_invalid")
        if order.get("amount") != DUMBBELL_AMOUNT_PAISE:
            raise RuntimeError("storefront_amount_invalid")
        if order.get("currency") != DUMBBELL_CURRENCY:
            raise RuntimeError("storefront_currency_invalid")
        receipt = order_receipt_for_idempotency_key(idempotency_key)
        try:
            provider_order = fetch_order_by_id(key_id, key_secret, order_id)
        except RazorpayProviderError as error:
            raise RuntimeError(error.diagnostic) from error
        matched = (
            provider_order.get("id") == order_id
            and provider_order.get("amount") == DUMBBELL_AMOUNT_PAISE
            and provider_order.get("currency") == DUMBBELL_CURRENCY
            and provider_order.get("receipt") == receipt
        )
        if not matched:
            raise RuntimeError("provider_order_direct_fetch_mismatch")
        reconciled_order, reconciliation_attempts = wait_for_receipt_reconciliation(
            key_id, key_secret, receipt, order_id
        )
        report = {
            "ready": True, "key_mode": "TEST", "webhook_secret_present": True,
            "webhook_secret_generated": generated, "local_server": True,
            "historical_population": {
                "payments": simulation.get("payments_total"),
                "captured": simulation.get("successes"),
                "failed": simulation.get("failures"),
                "findings": simulation.get("findings"),
            },
            "provider": {
                "test_mode_order_created": True, "provider_order_id_present": True,
                "direct_fetch_verified": True, "provider_round_trip": True,
                "receipt_reconciliation_verified": reconciled_order is not None,
                "receipt_reconciliation_attempts": reconciliation_attempts,
                "amount": DUMBBELL_AMOUNT_PAISE,
                "currency": DUMBBELL_CURRENCY,
            },
        }
        if reconciled_order is None:
            report["warnings"] = [
                "receipt reconciliation was not visible within the bounded probe window; "
                "direct order fetch passed"
            ]
        (runtime_dir / "provider-probe.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return report
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe genuine Razorpay Test Mode order API.")
    parser.add_argument("--credentials-file", required=True, type=Path)
    parser.add_argument("--runtime-dir", type=Path, default=DEFAULT_RUNTIME_DIR)
    parser.add_argument("--database-url", default="sqlite:///./genuine-testmode.db")
    parser.add_argument("--port", type=int, default=8011)
    parser.add_argument("--reset-db", action="store_true")
    args = parser.parse_args()
    try:
        report = run_probe(
            credentials_file=args.credentials_file, runtime_dir=args.runtime_dir,
            database_url=args.database_url, port=args.port, reset_db=args.reset_db,
        )
    except (RuntimeError, ValueError) as error:
        print(json.dumps({"ready": False, "error": str(error)}, indent=2))
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
