import hashlib
import hmac
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.tables import CheckoutOrder
from app.domain.models import CheckoutCallbackRequest, CheckoutFailureRequest, CheckoutOrderRequest
from app.integrations.razorpay import (
    DUMBBELL_AMOUNT_PAISE,
    DUMBBELL_CURRENCY,
    DUMBBELL_DESCRIPTION,
    DUMBBELL_PRODUCT_CODE,
    DUMBBELL_PRODUCT_NAME,
)

router = APIRouter(tags=["storefront"])
STOREFRONT_HTML = (
    Path(__file__).resolve().parent.parent / "templates" / "storefront.html"
).read_text(encoding="utf-8")


def _checkout_response(order: CheckoutOrder, key_id: str) -> dict[str, object]:
    return {
        "checkout_id": order.checkout_id,
        "idempotency_key": order.idempotency_key,
        "order_id": order.provider_order_id,
        "obligation_reference": order.obligation_reference,
        "key_id": key_id,
        "amount": order.amount,
        "currency": order.currency,
        "name": "ReRoute Dumbbell Store",
        "description": DUMBBELL_DESCRIPTION,
        "status": order.status,
    }


@router.get("/storefront", response_class=HTMLResponse, include_in_schema=False)
@router.get("/shop", response_class=HTMLResponse, include_in_schema=False)
def storefront_page() -> str:
    return STOREFRONT_HTML


@router.post(
    "/api/v1/orders",
    status_code=status.HTTP_201_CREATED,
    response_model=None,
)
@router.post(
    "/api/v1/storefront/orders",
    status_code=status.HTTP_201_CREATED,
    response_model=None,
    include_in_schema=False,
)
@router.post(
    "/api/v1/checkout/order",
    status_code=status.HTTP_201_CREATED,
    response_model=None,
    include_in_schema=False,
)
def create_storefront_order(
    request: Request, response: Response, payload: CheckoutOrderRequest | None = None
) -> dict[str, object]:
    header_idempotency_key = request.headers.get("Idempotency-Key")
    body_idempotency_key = payload.idempotency_key if payload else None
    if body_idempotency_key and header_idempotency_key:
        if body_idempotency_key != header_idempotency_key:
            raise HTTPException(status_code=409, detail="idempotency keys do not match")
    idempotency_key = body_idempotency_key or header_idempotency_key
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="idempotency key required")
    if len(idempotency_key) > 128:
        raise HTTPException(status_code=422, detail="idempotency key is too long")
    key_id = request.app.state.checkout_key_id
    if not key_id.startswith("rzp_test_"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Razorpay Test Mode order provider is not configured",
        )

    factory = request.app.state.session_factory
    checkout_id = f"checkout_{hashlib.sha256(idempotency_key.encode()).hexdigest()}"
    with factory() as session:
        existing = session.scalar(
            select(CheckoutOrder).where(CheckoutOrder.idempotency_key == idempotency_key)
        )
        if existing is not None and existing.provider_order_id:
            response.status_code = status.HTTP_200_OK
            return _checkout_response(existing, key_id)
        if existing is None:
            existing = CheckoutOrder(
                checkout_id=checkout_id,
                idempotency_key=idempotency_key,
                product_code=DUMBBELL_PRODUCT_CODE,
                product_name=DUMBBELL_PRODUCT_NAME,
                amount=DUMBBELL_AMOUNT_PAISE,
                currency=DUMBBELL_CURRENCY,
                status="creating",
                provider="razorpay_test",
                created_at=datetime.now(UTC),
            )
            session.add(existing)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = session.scalar(
                    select(CheckoutOrder).where(
                        CheckoutOrder.idempotency_key == idempotency_key
                    )
                )
                if existing is not None and existing.provider_order_id:
                    response.status_code = status.HTTP_200_OK
                    return _checkout_response(existing, key_id)
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="order is already being created",
                )
        elif existing.status == "creating":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="order is already being created",
            )
        else:
            existing.status = "creating"
            session.commit()

        try:
            provider_order = request.app.state.create_order(
                DUMBBELL_AMOUNT_PAISE, idempotency_key
            )
            provider_order_id = (
                provider_order
                if isinstance(provider_order, str)
                else provider_order.get("id")
            )
            if not isinstance(provider_order_id, str) or not provider_order_id:
                raise RuntimeError("invalid order provider response")
            if not provider_order_id.startswith("order_"):
                raise RuntimeError("invalid order provider response")
        except Exception as error:
            existing.status = "failed"
            session.commit()
            # Provider details, including account data, never cross this boundary.
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Razorpay Test Mode order creation failed",
            ) from error

        existing.provider_order_id = provider_order_id
        existing.obligation_reference = provider_order_id
        existing.status = "created"
        try:
            session.commit()
        except IntegrityError as error:
            session.rollback()
            existing.status = "failed"
            session.commit()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="provider order is already linked to another checkout",
            ) from error
        return _checkout_response(existing, key_id)


@router.post("/api/v1/checkout/callback", status_code=status.HTTP_202_ACCEPTED)
@router.post(
    "/api/v1/checkout/verify", status_code=status.HTTP_202_ACCEPTED, include_in_schema=False
)
def receive_checkout_callback(
    callback: CheckoutCallbackRequest, request: Request
) -> dict[str, str]:
    with request.app.state.session_factory() as session:
        order = session.scalar(
            select(CheckoutOrder).where(
                CheckoutOrder.provider_order_id == callback.razorpay_order_id
            )
        )
        if order is None:
            raise HTTPException(status_code=404, detail="checkout order not found")
        secret = request.app.state.checkout_key_secret
        expected = hmac.new(
            secret.encode(),
            f"{callback.razorpay_order_id}|{callback.razorpay_payment_id}".encode(),
            hashlib.sha256,
        ).hexdigest()
        if not secret or not hmac.compare_digest(expected, callback.razorpay_signature):
            raise HTTPException(status_code=401, detail="invalid checkout signature")
        if order.status not in {"payment_captured", "payment_failed"}:
            order.status = "callback_received"
            order.payment_id = callback.razorpay_payment_id
            session.commit()
    # This callback is only an integrity-checked client acknowledgement. The
    # signed webhook remains the authority for PaymentEvent and Outcome records.
    return {
        "order_id": callback.razorpay_order_id,
        "payment_id": callback.razorpay_payment_id,
        "status": "callback_received",
        "source": "checkout_callback",
    }


@router.post("/api/v1/checkout/failure", status_code=status.HTTP_202_ACCEPTED)
def receive_checkout_failure(
    failure: CheckoutFailureRequest, request: Request
) -> dict[str, str]:
    order_id = failure.order_id or failure.razorpay_order_id
    if not order_id:
        raise HTTPException(status_code=422, detail="order id required")
    with request.app.state.session_factory() as session:
        if session.scalar(
            select(CheckoutOrder.checkout_id).where(CheckoutOrder.provider_order_id == order_id)
        ) is None:
            raise HTTPException(status_code=404, detail="checkout order not found")
    # Browser failure callbacks are untrusted presentation signals. Deliberately
    # do not write a PaymentEvent or RecoveryCase here; Razorpay's signed webhook
    # is the only failure ingestion authority.
    return {"order_id": order_id, "status": "client_failure_received"}
