from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path


def fetch_dashboard(base_url: str) -> dict:
    request = urllib.request.Request(f"{base_url.rstrip('/')}/api/v1/dashboard")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"dashboard_http_status={error.code}") from error
    except Exception as error:
        raise RuntimeError(f"dashboard_request={type(error).__name__}") from error
    if not isinstance(body, dict):
        raise RuntimeError("dashboard_response_invalid")
    return body


def build_report(dashboard: dict) -> dict[str, object]:
    population = dashboard.get("population") or {}
    provider = dashboard.get("provider_evidence") or {}
    worklist = dashboard.get("worklist") or []
    timeline = dashboard.get("timeline") or []
    latest = population.get("latest_test_mode_payment")
    latest_case = None
    if isinstance(latest, dict):
        for case in worklist:
            if not isinstance(case, dict):
                continue
            if (
                latest.get("obligation_reference")
                and case.get("obligation_reference") == latest["obligation_reference"]
            ) or (latest.get("payment_id") and case.get("payment_id") == latest["payment_id"]):
                latest_case = case
                break

    case_timeline: list[dict] = []
    if latest_case:
        for item in timeline:
            if isinstance(item, dict) and item.get("case_id") == latest_case.get("case_id"):
                case_timeline = item.get("events") or []
                break
    action_events = [
        event.get("data") or {}
        for event in case_timeline
        if isinstance(event, dict) and event.get("kind") == "action"
    ]
    outcome_events = [
        event.get("data") or {}
        for event in case_timeline
        if isinstance(event, dict) and event.get("kind") == "outcome"
    ]
    latest_action = action_events[-1] if action_events else None
    outcome = outcome_events[-1] if outcome_events else None
    event_types = set(provider.get("event_types") or [])
    signed_failure = bool(provider.get("present")) and "payment.failed" in event_types
    signed_capture = bool(provider.get("present")) and "payment.captured" in event_types
    recovered = bool(
        outcome
        and outcome.get("recovered") is True
        and outcome.get("source") == "razorpay_test"
    )
    return {
        "population": {
            key: population.get(key, 0)
            for key in ("total", "captured", "failed", "test_mode_events")
        },
        "signed_evidence": {
            "present": bool(provider.get("present")),
            "signed_event_count": provider.get("signed_event_count", 0),
            "event_types": sorted(event_types),
            "payment_failed_present": signed_failure,
            "payment_captured_present": signed_capture,
            "raw_body_present": bool(provider.get("raw_body_present")),
            "checkout_order_owned": bool(provider.get("checkout_order_owned")),
            "provider_delivery_claim": provider.get("provider_delivery_claim"),
        },
        "recovery_case": {
            "present": latest_case is not None,
            "state": latest_case.get("state") if latest_case else None,
            "ranked_action_count": len(latest_case.get("ranked_actions") or [])
            if latest_case
            else 0,
        },
        "action": {
            "present": latest_action is not None,
            "tool": latest_action.get("tool") if latest_action else None,
            "status": latest_action.get("status") if latest_action else None,
            "provider_reference_present": bool(
                latest_action and latest_action.get("provider_reference")
            ),
        },
        "outcome": {
            "present": outcome is not None,
            "recovered": recovered,
            "recovered_amount": outcome.get("recovered_amount") if outcome else None,
            "source": outcome.get("source") if outcome else None,
        },
        "local_signed_failure_ready": signed_failure and latest_case is not None,
        "local_signed_recovery_ready": signed_failure and signed_capture and recovered,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export sanitized Test Mode evidence.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--write", type=Path)
    parser.add_argument("--require-failure", action="store_true")
    parser.add_argument("--require-recovery", action="store_true")
    args = parser.parse_args()
    try:
        report = build_report(fetch_dashboard(args.base_url))
    except RuntimeError as error:
        print(json.dumps({"ready": False, "error": str(error)}, indent=2))
        return 2
    encoded = json.dumps(report, indent=2, sort_keys=True)
    print(encoded)
    if args.write:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(encoded + "\n", encoding="utf-8")
    if args.require_recovery and not report["local_signed_recovery_ready"]:
        return 3
    if args.require_failure and not report["local_signed_failure_ready"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
