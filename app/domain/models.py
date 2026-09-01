from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import PaymentEventType, PaymentStatus


class NormalizedPaymentEvent(BaseModel):
    event_id: str
    provider_event_id: str
    event_type: PaymentEventType
    payment_id: str
    obligation_reference: str | None = None
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
    def from_razorpay(
        cls,
        payload: dict,
        raw_hash: str,
        webhook_event_id: str | None = None,
    ) -> "NormalizedPaymentEvent":
        event_type = payload["event"]
        payment = payload["payload"]["payment"]["entity"]
        payment_id = payment["id"]
        created_at = datetime.fromtimestamp(payment["created_at"], tz=UTC)
        # Prefer order_id, then notes obligation; fallback handled in state machine
        # PaymentObligation is explicit verified merchant reference
        notes = payment.get("notes", {})
        if isinstance(notes, dict):
            obligation_reference = (
                payment.get("order_id")
                or notes.get("obligation_reference")
                or notes.get("order_id")
            )
        else:
            obligation_reference = payment.get("order_id")
        # treat empty string as missing reference -> isolated attempt
        if obligation_reference == "":
            obligation_reference = None
        customer_id = notes.get("customer_id") if isinstance(notes, dict) else None
        return cls(
            event_id=f"evt_{webhook_event_id or payload.get('id', raw_hash)}",
            provider_event_id=webhook_event_id or payload.get("id", raw_hash),
            event_type=event_type,
            payment_id=payment_id,
            obligation_reference=obligation_reference,
            customer_id=customer_id,
            amount=payment["amount"],
            currency=payment["currency"],
            method=payment.get("method"),
            status=payment["status"],
            error_source=payment.get("error_source"),
            error_step=payment.get("error_step"),
            error_code=payment.get("error_code"),
            error_reason=payment.get("error_reason") or payment.get("error_description"),
            occurred_at=created_at,
            provider="razorpay_test",
            raw_hash=raw_hash,
        )


class CheckoutOrderRequest(BaseModel):
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class CheckoutCallbackRequest(BaseModel):
    razorpay_order_id: str = Field(min_length=1, max_length=128)
    razorpay_payment_id: str = Field(min_length=1, max_length=128)
    razorpay_signature: str = Field(min_length=1, max_length=128)


class CheckoutFailureRequest(BaseModel):
    order_id: str | None = Field(default=None, min_length=1, max_length=128)
    payment_id: str | None = Field(default=None, min_length=1, max_length=128)
    razorpay_order_id: str | None = Field(default=None, min_length=1, max_length=128)
    razorpay_payment_id: str | None = Field(default=None, min_length=1, max_length=128)


class PolicyResponse(BaseModel):
    allowed_actions: list[str]
    blocked_reasons: dict[str, list[str]]
    policy_version: str


class ActionRequest(BaseModel):
    action: Literal["payment_link", "contact", "retry", "promise", "escalate"]
    idempotency_key: str = Field(min_length=1, max_length=128)


class ActionResponse(BaseModel):
    action: str
    provider_reference: str | None
    status: str


class DecisionRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=128)
    approved: bool = False
    selected_action: Literal["payment_link", "contact", "retry", "promise", "escalate"] | None = (
        None
    )


class ResumeRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=128)


class StructuredDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_action: Literal["payment_link", "contact", "retry", "promise", "escalate"]


class DecisionResponse(BaseModel):
    decision_id: str
    selected_action: str
    selection_source: Literal["model", "fallback"]
    policy_version: str
    model_version: str
    evidence: dict
    action: ActionResponse | None


class PaymentExceptionRequest(BaseModel):
    kind: Literal["customer_debit_claim", "provider_reversal"]
    evidence: dict


class PaymentExceptionResolutionRequest(BaseModel):
    resolution: Literal["no_debit", "reversed", "captured", "refunded"]
    evidence: dict


class FindingAnalysisRequest(BaseModel):
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class PolicySettingsRequest(BaseModel):
    quiet_hours_start: int = Field(ge=0, le=23)
    quiet_hours_end: int = Field(ge=0, le=23)
    contact_limit: int = Field(ge=0, le=10)
    kill_switch: bool
    mock_identity: str = Field(min_length=1, max_length=128)


class MockInboxReplyRequest(BaseModel):
    reply: Literal["pay", "ignore", "promise", "help", "opt_out"]
