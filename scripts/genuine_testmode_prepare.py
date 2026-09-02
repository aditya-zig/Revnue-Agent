from __future__ import annotations

import argparse
import csv
import json
import os
import secrets
import shlex
import sys
from pathlib import Path

# Support direct execution as `python scripts/genuine_testmode_prepare.py`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings
from scripts.live_testmode_preflight import build_report

DEFAULT_RUNTIME_DIR = Path(".reroute-local")
DEFAULT_DATABASE_URL = "sqlite:///./genuine-testmode.db"


def _normalized(value: str) -> str:
    return value.strip().lower().replace(" ", "_").replace("-", "_")


def read_test_credentials(path: Path) -> tuple[str, str]:
    if not path.is_file():
        raise ValueError("credential file does not exist")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("credential CSV has no header")
        normalized_headers = {
            _normalized(header): header for header in reader.fieldnames if header
        }
        row = next(reader, None)
        if row is None:
            raise ValueError("credential CSV contains no credential row")

        def value_for(*names: str) -> str:
            for name in names:
                original = normalized_headers.get(_normalized(name))
                if original is not None:
                    value = (row.get(original) or "").strip()
                    if value:
                        return value
            return ""

        key_id = value_for("key_id", "key id", "razorpay_key_id", "razorpay key id")
        key_secret = value_for(
            "key_secret",
            "key secret",
            "razorpay_key_secret",
            "razorpay key secret",
            "secret",
        )
        if not key_id or not key_secret:
            non_empty_values = [
                str(value).strip()
                for value in row.values()
                if value is not None and str(value).strip()
            ]
            candidate_id = next(
                (value for value in non_empty_values if value.startswith("rzp_test_")),
                "",
            )
            if candidate_id:
                key_id = candidate_id
                key_secret = next(
                    (value for value in non_empty_values if value != candidate_id),
                    "",
                )
        if not key_id.startswith("rzp_test_"):
            raise ValueError("credential file does not contain a Razorpay Test Mode Key ID")
        if not key_secret:
            raise ValueError("credential file does not contain a Razorpay Key Secret")
        return key_id, key_secret


def ensure_runtime_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def ensure_webhook_secret(runtime_dir: Path) -> tuple[Path, str, bool]:
    path = runtime_dir / "webhook-secret"
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        if len(value) < 24:
            raise ValueError("existing local webhook secret is unexpectedly short")
        return path, value, False
    value = secrets.token_urlsafe(48)
    path.write_text(value + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path, value, True


def write_runtime_environment(
    runtime_dir: Path,
    *,
    key_id: str,
    key_secret: str,
    webhook_secret: str,
    database_url: str,
) -> Path:
    path = runtime_dir / "genuine-testmode.env"
    values = {
        "REROUTE_RAZORPAY_KEY_ID": key_id,
        "REROUTE_RAZORPAY_KEY_SECRET": key_secret,
        "REROUTE_RAZORPAY_WEBHOOK_SECRET": webhook_secret,
        "REROUTE_DATABASE_URL": database_url,
    }
    body = "\n".join(f"export {name}={shlex.quote(value)}" for name, value in values.items())
    path.write_text(body + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def _settings_for(*, key_id: str, key_secret: str, webhook_secret: str) -> Settings:
    names = (
        "REROUTE_RAZORPAY_KEY_ID",
        "REROUTE_RAZORPAY_KEY_SECRET",
        "REROUTE_RAZORPAY_WEBHOOK_SECRET",
    )
    old = {name: os.environ.get(name) for name in names}
    try:
        os.environ["REROUTE_RAZORPAY_KEY_ID"] = key_id
        os.environ["REROUTE_RAZORPAY_KEY_SECRET"] = key_secret
        os.environ["REROUTE_RAZORPAY_WEBHOOK_SECRET"] = webhook_secret
        return Settings()
    finally:
        for name, value in old.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def prepare(
    credentials_file: Path,
    runtime_dir: Path = DEFAULT_RUNTIME_DIR,
    database_url: str = DEFAULT_DATABASE_URL,
    public_url: str = "",
) -> dict[str, object]:
    runtime_dir = ensure_runtime_directory(runtime_dir)
    key_id, key_secret = read_test_credentials(credentials_file)
    webhook_secret_path, webhook_secret, generated = ensure_webhook_secret(runtime_dir)
    environment_path = write_runtime_environment(
        runtime_dir,
        key_id=key_id,
        key_secret=key_secret,
        webhook_secret=webhook_secret,
        database_url=database_url,
    )
    settings = _settings_for(
        key_id=key_id,
        key_secret=key_secret,
        webhook_secret=webhook_secret,
    )
    return {
        "key_id_present": True,
        "key_mode": "TEST",
        "key_secret_present": True,
        "webhook_secret_present": True,
        "webhook_secret_generated": generated,
        "runtime_env": str(environment_path),
        "webhook_secret_file": str(webhook_secret_path),
        "database_url": database_url,
        "public_url_present": bool(public_url),
        "preflight": build_report(settings, public_url),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare a local Razorpay Test Mode proof environment without printing secrets."
    )
    parser.add_argument("--credentials-file", required=True, type=Path)
    parser.add_argument("--runtime-dir", type=Path, default=DEFAULT_RUNTIME_DIR)
    parser.add_argument("--database-url", default=DEFAULT_DATABASE_URL)
    parser.add_argument("--public-url", default="")
    args = parser.parse_args()
    try:
        report = prepare(
            credentials_file=args.credentials_file,
            runtime_dir=args.runtime_dir,
            database_url=args.database_url,
            public_url=args.public_url,
        )
    except ValueError as error:
        print(json.dumps({"ready": False, "error": str(error)}, indent=2))
        return 2
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
