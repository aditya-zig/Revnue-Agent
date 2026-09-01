"""Deterministic synthetic PaymentEvent generation for the local demo.

The default corpus is the Issue #47 historical-payment population: exactly 999
rows with a planted UPI failure cohort.  The generator only emits normalized
CSV; importing it through ``app.ingestion.csv_loader.import_csv`` preserves the
normal ``csv_import`` PaymentEvent provenance and existing case/policy rules.
"""

import csv
import random
from datetime import UTC, datetime, timedelta
from io import StringIO

FIELDNAMES = [
    "event_id",
    "event_type",
    "payment_id",
    "obligation_reference",
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

HISTORICAL_PAYMENT_COUNT = 999
DEFAULT_SEED = 47
_RESERVED_ROW_COUNT = 7
_GENERATED_ROW_COUNT = HISTORICAL_PAYMENT_COUNT - _RESERVED_ROW_COUNT
# The UPI cohort is deliberately large enough to rank by recoverable impact,
# while its 50% failure rate remains a cohort property rather than an 800/999
# population-wide failure artifact.
_GENERATED_METHOD_COUNTS = {"upi": 446, "card": 273, "netbanking": 273}
_GENERATED_FAILURE_COUNTS = {"upi": 221, "card": 11, "netbanking": 11}
_TARGET_FAILURE_COUNT = 250
_FAILURE_REASONS = ["insufficient funds", "payment failed", "bank declined", "network error"]
_ERROR_SOURCES = ["bank", "gateway"]
_ERROR_STEPS = ["payment_authorization", "payment_verification", "payment_routing"]
_ERROR_CODES = ["BAD_REQUEST_ERROR", "NETWORK_ERROR", "GATEWAY_ERROR"]
_AMOUNTS = [49900, 99900, 149900, 249900, 499900]


def generate_csv(seed: int = DEFAULT_SEED, event_count: int = HISTORICAL_PAYMENT_COUNT) -> str:
    """Generate a reproducible normalized CSV population.

    ``event_count=999`` is the supported Issue #47 demo/test contract.  The
    count remains configurable for small local fixtures; those populations use
    the same seeded shape and are not the historical 999-row evidence.
    """
    if event_count < _RESERVED_ROW_COUNT:
        raise ValueError(f"event_count must be at least {_RESERVED_ROW_COUNT}")

    random_source = random.Random(seed)
    generated_count = event_count - _RESERVED_ROW_COUNT
    assignments = _assignments(generated_count, random_source)
    rows = [
        _generated_row(index, method, status, random_source)
        for index, (method, status) in enumerate(assignments)
    ]
    rows.extend(
        [
            _hard_decline_row(),
            _provider_failure_row(),
            _opt_out_row(),
            _promise_row(),
            _eligible_row(),
            _isolated_row("a"),
            _isolated_row("b"),
        ]
    )
    # Assignment is shuffled, but serialization is intentionally stable: the
    # generated event IDs are in numeric order and named edge rows are audited
    # at the end of the file.
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=FIELDNAMES, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def _assignments(generated_count: int, random_source: random.Random) -> list[tuple[str, str]]:
    """Return a seeded, order-independent method/status assignment list."""
    method_counts = _scaled_counts(_GENERATED_METHOD_COUNTS, _GENERATED_ROW_COUNT, generated_count)
    target_failures = (
        round(
            (generated_count + _RESERVED_ROW_COUNT)
            * _TARGET_FAILURE_COUNT
            / HISTORICAL_PAYMENT_COUNT
        )
        - _RESERVED_ROW_COUNT
    )
    target_failures = max(0, min(generated_count, target_failures))
    failure_counts = _scaled_counts(
        _GENERATED_FAILURE_COUNTS, sum(_GENERATED_FAILURE_COUNTS.values()), target_failures
    )
    # Rounding each method independently can leave one row unassigned.  Put the
    # remainder in netbanking, the least interesting comparison cohort.
    failure_counts["netbanking"] = target_failures - failure_counts["upi"] - failure_counts["card"]
    if not 0 <= failure_counts["netbanking"] <= method_counts["netbanking"]:
        raise ValueError("event_count is too small for the configured simulator shape")

    assignments: list[tuple[str, str]] = []
    for method in ("upi", "card", "netbanking"):
        failures = failure_counts[method]
        successes = method_counts[method] - failures
        assignments.extend((method, "failed") for _ in range(failures))
        assignments.extend((method, "captured") for _ in range(successes))
    random_source.shuffle(assignments)
    return assignments


def _scaled_counts(
    counts: dict[str, int], original_total: int, target_total: int
) -> dict[str, int]:
    scaled = {key: round(value * target_total / original_total) for key, value in counts.items()}
    largest_key = max(counts, key=lambda key: counts[key])
    scaled[largest_key] += target_total - sum(scaled.values())
    return scaled


def _generated_row(
    index: int, method: str, status: str, random_source: random.Random
) -> dict[str, str]:
    customer_number = index
    occurred_at = datetime(2026, 8, 1, tzinfo=UTC) + timedelta(minutes=index * 13)
    payment_id = f"demo_generated_{index:05d}"
    is_failure = status == "failed"
    if is_failure:
        error_reason = random_source.choice(_FAILURE_REASONS)
        error_code = random_source.choice(_ERROR_CODES)
        error_source = random_source.choice(_ERROR_SOURCES)
        error_step = random_source.choice(_ERROR_STEPS)
        # Split sequence labels across independent rows so no single
        # failure-only detector cohort dominates the planted method cohort.
        prior_failures = "1" if random_source.random() < 0.5 else "0"
        event_type = "payment.failed"
    else:
        error_reason = ""
        error_code = ""
        error_source = ""
        error_step = ""
        prior_failures = "0"
        event_type = "payment.captured"

    return {
        "event_id": f"demo_event_{index:05d}",
        "event_type": event_type,
        "payment_id": payment_id,
        "obligation_reference": f"order_{index:05d}",
        "customer_id": f"demo_customer_{customer_number:03d}",
        "amount": str(random_source.choice(_AMOUNTS)),
        "currency": "INR",
        "method": method,
        "status": status,
        "error_source": error_source,
        "error_step": error_step,
        "error_code": error_code,
        "error_reason": error_reason,
        "occurred_at": occurred_at.isoformat(),
        "tenure_days": str(30 + (customer_number * 17) % 720),
        "successful_payments": str(customer_number % 16),
        "prior_failures": prior_failures,
        "preferred_method": method,
        "consent": "true" if customer_number % 11 else "false",
        "locale": "en-IN" if customer_number % 3 else "hi-IN",
    }


def _hard_decline_row() -> dict[str, str]:
    return {
        "event_id": "demo_hard_decline",
        "event_type": "payment.failed",
        "payment_id": "demo_hard_decline",
        "obligation_reference": "order_hard_decline",
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
        "obligation_reference": "order_provider_failure",
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
        "obligation_reference": "order_opt_out",
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
        "obligation_reference": "order_promise",
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


def _eligible_row() -> dict[str, str]:
    return {
        "event_id": "demo_eligible",
        "event_type": "payment.failed",
        "payment_id": "demo_eligible",
        "obligation_reference": "order_eligible",
        "customer_id": "demo_customer_eligible",
        "amount": "199900",
        "currency": "INR",
        "method": "netbanking",
        "status": "failed",
        "error_source": "bank",
        "error_step": "payment_authorization",
        "error_code": "BAD_REQUEST_ERROR",
        "error_reason": "insufficient funds",
        "occurred_at": "2026-08-24T04:35:00+00:00",
        "tenure_days": "400",
        "successful_payments": "20",
        "prior_failures": "2",
        "preferred_method": "netbanking",
        "consent": "true",
        "locale": "en-IN",
    }


def _isolated_row(suffix: str) -> dict[str, str]:
    return {
        "event_id": f"demo_isolated_{suffix}",
        "event_type": "payment.failed",
        "payment_id": f"demo_isolated_{suffix}",
        "obligation_reference": "",
        "customer_id": f"demo_customer_isolated_{suffix}",
        "amount": "100000",
        "currency": "INR",
        "method": "upi",
        "status": "failed",
        "error_source": "gateway",
        "error_step": "payment_authorization",
        "error_code": "BAD_REQUEST_ERROR",
        "error_reason": "insufficient funds",
        "occurred_at": f"2026-08-24T04:4{0 if suffix == 'a' else 5}:00+00:00",
        "tenure_days": "90",
        "successful_payments": "3",
        "prior_failures": "0",
        "preferred_method": "upi",
        "consent": "true",
        "locale": "en-IN",
    }
