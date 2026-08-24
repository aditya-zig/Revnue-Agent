from datetime import UTC, datetime

from pydantic import BaseModel, Field

from app.domain.enums import PaymentEventType, PaymentStatus


class NormalizedPaymentEvent(BaseModel):
    event_id: str
    provider_event_id: str
    event_type: PaymentEventType
    payment_id: str
    customer_id: str | None = None
    amount: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    method: str | None = None
    status: PaymentStatus
    error_source: str | None = None
    error_step: str | None = None
    error_code: str | None = None
    error_reason: str | None = None
    occurred_at: datetime
    provider: str
    raw_hash: str
    raw_body: bytes | None = None

    @classmethod
    def from_razorpay(cls, payload: dict, raw_hash: str) -> "NormalizedPaymentEvent":
        event_type = payload["event"]
        payment = payload["payload"]["payment"]["entity"]
        payment_id = payment["id"]
        created_at = datetime.fromtimestamp(payment["created_at"], tz=UTC)
        return cls(
            event_id=f"evt_{payload.get('id', raw_hash)}",
            provider_event_id=payload.get("id", raw_hash),
            event_type=event_type,
            payment_id=payment_id,
            customer_id=payment.get("notes", {}).get("customer_id"),
            amount=payment["amount"],
            currency=payment["currency"],
            method=payment.get("method"),
            status=payment["status"],
            error_source=payment.get("error_source"),
            error_step=payment.get("error_step"),
            error_code=payment.get("error_code"),
            error_reason=payment.get("error_description"),
            occurred_at=created_at,
            provider="razorpay_test",
            raw_hash=raw_hash,
        )


class PolicyResponse(BaseModel):
    allowed_actions: list[str]
    blocked_reasons: dict[str, list[str]]
    policy_version: str
