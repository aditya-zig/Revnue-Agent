"""Deterministic merchant-day replay fixtures for ReRoute Sentinel.

These events are deliberately synthetic. They use the normalized PaymentEvent
contract but never claim provider authenticity. The primary scenario contains
one planted PSP/UPI degradation plus a few non-recoverable card hard declines.
"""

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Literal

from app.domain.enums import EvidenceSource, PaymentEventType, PaymentStatus
from app.domain.models import NormalizedPaymentEvent

ScenarioName = Literal["primary", "healthy"]

DEFAULT_SEED = 47
DEFAULT_REPLAY_ID = "merchant_day_demo"
TOTAL_EVENTS = 300
BASELINE_EVENTS = 150
INCIDENT_EVENTS = 90
RECOVERY_EVENTS = TOTAL_EVENTS - BASELINE_EVENTS - INCIDENT_EVENTS
EVENT_SPACING = timedelta(minutes=2)
PROVIDER = "simulated_psp_a"
METHODS = ("upi", "card", "netbanking")
AMOUNTS = (49900, 99900, 149900, 199900, 249900, 499900)


@dataclass(frozen=True)
class PlantedIncident:
    provider: str
    method: str
    starts_at: datetime
    ends_at: datetime


@dataclass(frozen=True)
class MerchantDay:
    replay_id: str
    run_id: str
    seed: int
    scenario: ScenarioName
    events: tuple[NormalizedPaymentEvent, ...]
    planted_incidents: tuple[PlantedIncident, ...]


def generate_merchant_day(
    *,
    seed: int = DEFAULT_SEED,
    replay_id: str = DEFAULT_REPLAY_ID,
    scenario: ScenarioName = "primary",
    run_id: str | None = None,
) -> MerchantDay:
    """Generate one reproducible merchant day ordered by replay event time."""

    if seed < 0:
        raise ValueError("seed must be non-negative")
    _validate_identifier(replay_id, field="replay_id")
    namespace = run_id or f"replay_{replay_id}_s{seed}"
    _validate_identifier(namespace, field="run_id")

    random_source = random.Random(seed)
    start = datetime(2026, 9, 4, 3, 30, tzinfo=UTC)
    method_ordinals = {method: 0 for method in METHODS}
    events: list[NormalizedPaymentEvent] = []

    for index in range(TOTAL_EVENTS):
        method = METHODS[index % len(METHODS)]
        method_ordinal = method_ordinals[method]
        method_ordinals[method] += 1
        occurred_at = start + EVENT_SPACING * index
        phase = _phase(index)
        status, error_code, error_reason = _outcome(
            scenario=scenario,
            phase=phase,
            method=method,
            method_ordinal=method_ordinal,
            seed=seed,
        )
        amount = random_source.choice(AMOUNTS)
        event_type = (
            PaymentEventType.CAPTURED
            if status == PaymentStatus.CAPTURED
            else PaymentEventType.FAILED
        )
        provider_event_id = f"{namespace}_provider_event_{index:03d}"
        payment_id = f"{namespace}_payment_{index:03d}"
        merchant_order_reference = f"{namespace}_merchant_order_{index:03d}"
        provider_order_id = f"{namespace}_provider_order_{index:03d}"
        digest = sha256(
            "|".join(
                [
                    namespace,
                    scenario,
                    str(index),
                    provider_event_id,
                    payment_id,
                    str(amount),
                    method,
                    status.value,
                    error_code or "",
                    occurred_at.isoformat(),
                ]
            ).encode()
        ).hexdigest()
        events.append(
            NormalizedPaymentEvent(
                event_id=f"evt_{provider_event_id}",
                provider_event_id=provider_event_id,
                event_type=event_type,
                payment_id=payment_id,
                obligation_reference=merchant_order_reference,
                merchant_order_reference=merchant_order_reference,
                provider_order_id=provider_order_id,
                customer_id=f"{namespace}_customer_{index:03d}",
                amount=amount,
                currency="INR",
                method=method,
                status=status,
                error_source="provider" if status == PaymentStatus.FAILED else None,
                error_step=(
                    "payment_authorization"
                    if status == PaymentStatus.FAILED
                    else None
                ),
                error_code=error_code,
                error_reason=error_reason,
                occurred_at=occurred_at,
                provider=PROVIDER,
                source_kind=EvidenceSource.SIMULATED_PROVIDER,
                authenticity_verified=False,
                raw_hash=digest,
                raw_body=None,
            )
        )

    planted: tuple[PlantedIncident, ...] = ()
    if scenario == "primary":
        planted = (
            PlantedIncident(
                provider=PROVIDER,
                method="upi",
                starts_at=events[BASELINE_EVENTS].occurred_at,
                ends_at=events[BASELINE_EVENTS + INCIDENT_EVENTS - 1].occurred_at,
            ),
        )
    return MerchantDay(
        replay_id=replay_id,
        run_id=namespace,
        seed=seed,
        scenario=scenario,
        events=tuple(events),
        planted_incidents=planted,
    )


def _validate_identifier(value: str, *, field: str) -> None:
    if not value or not value.replace("_", "").replace("-", "").isalnum():
        raise ValueError(
            f"{field} must contain only letters, numbers, hyphens, or underscores"
        )


def _phase(index: int) -> str:
    if index < BASELINE_EVENTS:
        return "baseline"
    if index < BASELINE_EVENTS + INCIDENT_EVENTS:
        return "incident"
    return "recovery"


def _outcome(
    *,
    scenario: ScenarioName,
    phase: str,
    method: str,
    method_ordinal: int,
    seed: int,
) -> tuple[PaymentStatus, str | None, str | None]:
    if scenario == "primary" and phase == "incident" and method == "upi":
        incident_ordinal = method_ordinal - BASELINE_EVENTS // len(METHODS)
        if incident_ordinal % 3 != 0:
            return (
                PaymentStatus.FAILED,
                "GATEWAY_ERROR",
                "temporary_provider_failure",
            )

    if scenario == "primary" and phase == "incident" and method == "card":
        incident_ordinal = method_ordinal - BASELINE_EVENTS // len(METHODS)
        if incident_ordinal % 6 == 0:
            return PaymentStatus.FAILED, "HARD_DECLINE", "hard_decline"

    # Healthy traffic has a stable deterministic background failure rate. The
    # seed shifts positions without introducing random statistical luck.
    period = {"upi": 13, "card": 17, "netbanking": 19}[method]
    if (method_ordinal + seed) % period == 0:
        return PaymentStatus.FAILED, "PAYMENT_FAILED", "payment_failed"
    return PaymentStatus.CAPTURED, None, None
