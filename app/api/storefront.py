import hashlib
import hmac
import inspect
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from app.db.tables import CheckoutOrder
from app.domain.models import CheckoutCallbackRequest, CheckoutFailureRequest, CheckoutOrderRequest
from app.integrations.razorpay import (
    DUMBBELL_AMOUNT_PAISE,
    DUMBBELL_CURRENCY,
    DUMBBELL_DESCRIPTION,
    DUMBBELL_PRODUCT_CODE,
    DUMBBELL_PRODUCT_NAME,
    ORDER_PROVIDER_ERROR,
    order_receipt_for_idempotency_key,
)

router = APIRouter(tags=["storefront"])
ORDER_CREATING_STALE_AFTER = timedelta(minutes=5)
ORDER_RECONCILIATION_UNAVAILABLE = "order provider reconciliation is unavailable"
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


def _provider_order_id(provider_order: object) -> str | None:
    candidate = (
        provider_order
        if isinstance(provider_order, str)
        else provider_order.get("id")
        if isinstance(provider_order, dict)
        else None
    )
    if isinstance(candidate, str) and candidate.startswith("order_"):
        return candidate
    return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _call_order_reconciler(reconciler, order: CheckoutOrder, receipt: str):
    """Call old injected seams safely while passing persisted order terms when supported."""
    try:
        parameters = inspect.signature(reconciler).parameters.values()
    except (TypeError, ValueError):
        return reconciler(receipt)
    positional = [
        parameter
        for parameter in parameters
        if parameter.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    accepts_terms = any(
        parameter.kind == inspect.Parameter.VAR_POSITIONAL for parameter in parameters
    ) or len(positional) >= 3
    if accepts_terms:
        return reconciler(receipt, order.amount, order.currency)
    return reconciler(receipt)


def _validated_reconciled_order_id(
    provider_order: object, order: CheckoutOrder, receipt: str
) -> str:
    if not isinstance(provider_order, dict):
        raise ValueError("order reconciliation response is not an order")
    if (
        type(provider_order.get("receipt")) is not str
        or provider_order["receipt"] != receipt
        or type(provider_order.get("amount")) is not int
        or provider_order["amount"] != order.amount
        or type(provider_order.get("currency")) is not str
        or provider_order["currency"] != order.currency
    ):
        raise ValueError("order reconciliation terms do not match")
    provider_order_id = _provider_order_id(provider_order)
    if provider_order_id is None:
        raise ValueError("order reconciliation response has no valid provider ID")
    return provider_order_id


def _claim_order_for_attempt(session, order: CheckoutOrder, now: datetime) -> bool:
    if order.status == "failed":
        claim = session.execute(
            update(CheckoutOrder)
            .execution_options(synchronize_session=False)
            .where(
                CheckoutOrder.checkout_id == order.checkout_id,
                CheckoutOrder.status == "failed",
            )
            .values(status="creating", creating_started_at=now)
        )
    elif order.status == "creating":
        started_at = _as_utc(order.creating_started_at or order.created_at)
        if started_at > now - ORDER_CREATING_STALE_AFTER:
            return False
        cutoff = now - ORDER_CREATING_STALE_AFTER
        claim = session.execute(
            update(CheckoutOrder)
            .execution_options(synchronize_session=False)
            .where(
                CheckoutOrder.checkout_id == order.checkout_id,
                CheckoutOrder.status == "creating",
                or_(
                    CheckoutOrder.creating_started_at.is_(None),
                    CheckoutOrder.creating_started_at <= cutoff,
                ),
            )
            .values(creating_started_at=now)
        )
    else:
        return False
    if claim.rowcount != 1:
        session.rollback()
        return False
    session.commit()
    session.refresh(order)
    return True


def _link_provider_order(
    session, order: CheckoutOrder, provider_order_id: str
) -> None:
    order.provider_order_id = provider_order_id
    order.obligation_reference = provider_order_id
    order.status = "created"
    order.creating_started_at = None
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        current = session.get(CheckoutOrder, order.checkout_id)
        if current is not None:
            current.status = "creating"
            current.creating_started_at = datetime.now(UTC)
            session.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="provider order is already linked to another checkout",
        ) from error


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
    receipt = order_receipt_for_idempotency_key(idempotency_key)
    now = datetime.now(UTC)
    with factory() as session:
        existing = session.scalar(
            select(CheckoutOrder).where(CheckoutOrder.idempotency_key == idempotency_key)
        )
        is_new = existing is None
        if existing is not None and (
            existing.amount != DUMBBELL_AMOUNT_PAISE
            or existing.currency != DUMBBELL_CURRENCY
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="checkout order amount or currency is invalid",
            )
        if existing is not None and existing.provider_order_id:
            response.status_code = status.HTTP_200_OK
            return _checkout_response(existing, key_id)
        if existing is None:
            existing = CheckoutOrder(
                checkout_id=checkout_id,
                idempotency_key=idempotency_key,
                provider_receipt=receipt,
                product_code=DUMBBELL_PRODUCT_CODE,
                product_name=DUMBBELL_PRODUCT_NAME,
                amount=DUMBBELL_AMOUNT_PAISE,
                currency=DUMBBELL_CURRENCY,
                status="creating",
                creating_started_at=now,
                provider="razorpay_test",
                created_at=now,
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
                is_new = False
                if existing is not None and existing.provider_order_id:
                    response.status_code = status.HTTP_200_OK
                    return _checkout_response(existing, key_id)
                if existing is None:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="order is already being created",
                    )
        if existing.provider_receipt is None:
            existing.provider_receipt = receipt
            session.commit()

        # A fresh lease belongs to another request. A stale lease is recovered
        # only after looking up the deterministic receipt, so an uncertain POST
        # can never be repeated blindly.
        if not is_new and not _claim_order_for_attempt(session, existing, now):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="order is already being created",
            )

        reconciler = request.app.state.find_order_by_receipt
        if not is_new and existing.provider_order_id is None:
            if reconciler is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=ORDER_RECONCILIATION_UNAVAILABLE,
                )
            reconciliation_receipt = existing.provider_receipt or receipt
            try:
                reconciled = _call_order_reconciler(
                    reconciler, existing, reconciliation_receipt
                )
                reconciled_id = (
                    None
                    if reconciled is None
                    else _validated_reconciled_order_id(
                        reconciled, existing, reconciliation_receipt
                    )
                )
            except Exception as error:
                # Keep the creating lease. A later request can retry reconciliation,
                # but must not issue a second provider POST while it is unknown or
                # the provider response is malformed/ambiguous.
                existing.creating_started_at = datetime.now(UTC)
                session.commit()
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=ORDER_PROVIDER_ERROR,
                ) from error
            if reconciled_id is not None:
                _link_provider_order(session, existing, reconciled_id)
                response.status_code = status.HTTP_200_OK
                return _checkout_response(existing, key_id)

        try:
            provider_order = request.app.state.create_order(
                DUMBBELL_AMOUNT_PAISE, idempotency_key
            )
            provider_order_id = _provider_order_id(provider_order)
            if provider_order_id is None:
                raise RuntimeError("invalid order provider response")
        except Exception as error:
            # Unless the provider explicitly classifies a failure as known-safe,
            # retain creating status: the request may have been accepted remotely.
            uncertain = getattr(error, "uncertain", True)
            existing.status = "creating" if uncertain else "failed"
            existing.creating_started_at = datetime.now(UTC) if uncertain else None
            session.commit()
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=ORDER_PROVIDER_ERROR,
            ) from error

        _link_provider_order(session, existing, provider_order_id)
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
