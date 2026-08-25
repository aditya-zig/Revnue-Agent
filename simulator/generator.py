import csv
import random
from datetime import UTC, datetime, timedelta
from io import StringIO

FIELDNAMES = [
    "event_id",
    "event_type",
    "payment_id",
    "customer_id",
    "amount",
    "currency",
    "method",
    "status",
    "error_source",
    "error_step",
    "error_code",
    "error_reason",
    "occurred_at",
    "tenure_days",
    "successful_payments",
    "prior_failures",
    "preferred_method",
    "consent",
    "locale",
]


def generate_csv(seed: int = 7, event_count: int = 500) -> str:
    if event_count < 4:
        raise ValueError("event_count must leave room for the named edge cases")

    random_source = random.Random(seed)
    rows = [_generated_row(index, random_source) for index in range(event_count - 4)]
    rows.extend([_hard_decline_row(), _provider_failure_row(), _opt_out_row(), _promise_row()])

    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=FIELDNAMES, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def _generated_row(index: int, random_source: random.Random) -> dict[str, str]:
    customer_number = index % 180
    method = random_source.choice(["upi", "upi", "card", "netbanking"])
    is_failure = index % 5 != 0
    occurred_at = datetime(2026, 8, 1, tzinfo=UTC) + timedelta(minutes=index * 13)
    amount = random_source.choice([49900, 99900, 149900, 249900, 499900])
    payment_id = f"demo_generated_{index:03d}"
    customer_id = f"demo_customer_{customer_number:03d}"
    if is_failure:
        error_reason = random_source.choice(
            ["insufficient funds", "payment failed", "bank declined", "network error"]
        )
        error_code = "BAD_REQUEST_ERROR"
        status = "failed"
        event_type = "payment.failed"
        error_source = "bank" if method == "upi" else "gateway"
        error_step = "payment_authorization"
    else:
        error_reason = ""
        error_code = ""
        status = "captured"
        event_type = "payment.captured"
        error_source = ""
        error_step = ""

    return {
        "event_id": f"demo_event_{index:03d}",
        "event_type": event_type,
        "payment_id": payment_id,
        "customer_id": customer_id,
        "amount": str(amount),
        "currency": "INR",
        "method": method,
        "status": status,
        "error_source": error_source,
        "error_step": error_step,
        "error_code": error_code,
        "error_reason": error_reason,
        "occurred_at": occurred_at.isoformat(),
        "tenure_days": str(30 + (customer_number * 17) % 720),
        "successful_payments": str(1 + customer_number % 16),
        "prior_failures": str(customer_number % 4),
        "preferred_method": method,
        "consent": "true" if customer_number % 11 else "false",
        "locale": "hi-IN" if customer_number % 3 else "en-IN",
    }


def _hard_decline_row() -> dict[str, str]:
    return {
        "event_id": "demo_hard_decline",
        "event_type": "payment.failed",
        "payment_id": "demo_hard_decline",
        "customer_id": "demo_customer_hard_decline",
        "amount": "249900",
        "currency": "INR",
        "method": "card",
        "status": "failed",
        "error_source": "gateway",
        "error_step": "payment_authorization",
        "error_code": "HARD_DECLINE",
        "error_reason": "card declined",
        "occurred_at": "2026-08-24T04:15:00+00:00",
        "tenure_days": "365",
        "successful_payments": "12",
        "prior_failures": "0",
        "preferred_method": "card",
        "consent": "true",
        "locale": "en-IN",
    }


def _provider_failure_row() -> dict[str, str]:
    return {
        "event_id": "demo_provider_failure",
        "event_type": "payment.failed",
        "payment_id": "demo_provider_failure",
        "customer_id": "demo_customer_provider_failure",
        "amount": "249900",
        "currency": "INR",
        "method": "upi",
        "status": "failed",
        "error_source": "gateway",
        "error_step": "payment_authorization",
        "error_code": "BAD_REQUEST_ERROR",
        "error_reason": "insufficient funds",
        "occurred_at": "2026-08-24T04:20:00+00:00",
        "tenure_days": "240",
        "successful_payments": "10",
        "prior_failures": "0",
        "preferred_method": "upi",
        "consent": "true",
        "locale": "en-IN",
    }


def _opt_out_row() -> dict[str, str]:
    return {
        "event_id": "demo_opt_out",
        "event_type": "payment.failed",
        "payment_id": "demo_opt_out",
        "customer_id": "demo_customer_opt_out",
        "amount": "149900",
        "currency": "INR",
        "method": "upi",
        "status": "failed",
        "error_source": "bank",
        "error_step": "payment_authorization",
        "error_code": "BAD_REQUEST_ERROR",
        "error_reason": "insufficient funds",
        "occurred_at": "2026-08-24T04:25:00+00:00",
        "tenure_days": "180",
        "successful_payments": "5",
        "prior_failures": "1",
        "preferred_method": "upi",
        "consent": "false",
        "locale": "en-IN",
    }


def _promise_row() -> dict[str, str]:
    return {
        "event_id": "demo_promise",
        "event_type": "payment.failed",
        "payment_id": "demo_promise",
        "customer_id": "demo_customer_promise",
        "amount": "99900",
        "currency": "INR",
        "method": "card",
        "status": "failed",
        "error_source": "gateway",
        "error_step": "payment_authorization",
        "error_code": "BAD_REQUEST_ERROR",
        "error_reason": "payment failed",
        "occurred_at": "2026-08-24T04:30:00+00:00",
        "tenure_days": "300",
        "successful_payments": "14",
        "prior_failures": "0",
        "preferred_method": "card",
        "consent": "true",
        "locale": "hi-IN",
    }
