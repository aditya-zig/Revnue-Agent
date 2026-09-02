from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

# Ensure repo root is on sys.path when run as `python scripts/...` without PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings  # noqa: E402

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _public_https_url(value: str) -> bool:
    if not value:
        return False
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and bool(host) and host not in LOCAL_HOSTS


def build_report(settings: Settings, public_url: str = "") -> dict[str, object]:
    key_id_ready = settings.razorpay_key_id.startswith("rzp_test_")
    key_secret_ready = bool(settings.razorpay_key_secret)
    webhook_secret_ready = bool(settings.razorpay_webhook_secret)
    public_url_ready = _public_https_url(public_url)
    webhook_url = f"{public_url.rstrip('/')}/api/v1/webhooks/razorpay" if public_url_ready else ""

    checks = {
        "test_mode_key_id": key_id_ready,
        "key_secret_configured": key_secret_ready,
        "webhook_secret_configured": webhook_secret_ready,
        "public_https_url": public_url_ready,
    }
    return {
        "ready": all(checks.values()),
        "checks": checks,
        "webhook_url": webhook_url,
        "required_events": ["payment.failed", "payment.captured"],
        "storefront_path": "/storefront",
        "notes": [
            "Secrets are never printed by this preflight.",
            (
                "Browser callbacks are presentation-only; "
                "signed Razorpay webhooks remain authoritative."
            ),
            (
                "The webhook secret is merchant-chosen; it does not need "
                "to equal the Razorpay API Key Secret."
            ),
            (
                "ReRoute can prove signature acceptance locally; "
                "provider-delivery provenance must be verified separately."
            ),
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Check whether the local ReRoute app is configured "
            "for the genuine Razorpay Test Mode demo."
        ),
    )
    parser.add_argument(
        "--public-url",
        default="",
        help="Public HTTPS base URL that forwards to the local ReRoute app.",
    )
    args = parser.parse_args()

    report = build_report(Settings(), args.public_url)
    print(json.dumps(report, indent=2))
    return 0 if report["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
