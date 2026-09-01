import hashlib
import hmac
import json
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.db.tables import CheckoutOrder, Outcome, PaymentEvent, RecoveryCase
from app.main import create_app

CHECKOUT_SECRET = "checkout-test-secret"
WEBHOOK_SECRET = "webhook-test-secret"


def signed(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def webhook(
    event: str,
    order_id: str | None,
    payment_id: str,
    status: str,
    payment_link_id: str | None = None,
) -> bytes:
    entity = {
        "id": payment_id,
        "amount": 249900,
        "currency": "INR",
        "status": status,
        "created_at": 1724481000 if status == "failed" else 1724481100,
        "method": "card",
        "notes": {"customer_id": "cust_dumbbell"},
    }
    if order_id is not None:
        entity["order_id"] = order_id
    if payment_link_id is not None:
        entity["payment_link_id"] = payment_link_id
    if status == "failed":
        entity.update(
            {
                "error_code": "BAD_REQUEST_ERROR",
                "error_description": "payment failed",
            }
        )
    return json.dumps(
        {
            "id": f"provider_{payment_id}",
            "event": event,
            "payload": {"payment": {"entity": entity}},
        },
        separators=(",", ":"),
    ).encode()


@pytest.fixture
def app(database_url):
    created_orders: list[tuple[int, str]] = []
    created_links: list[tuple[int, str]] = []

    def create_order(amount: int, idempotency_key: str) -> str:
        created_orders.append((amount, idempotency_key))
        return "order_dumbbell_test"

    def create_payment_link(amount: int, idempotency_key: str) -> str:
        created_links.append((amount, idempotency_key))
        return "plink_dumbbell_test"

    app = create_app(
        database_url=database_url,
        webhook_secret=WEBHOOK_SECRET,
        razorpay_key_id="rzp_test_local",
        razorpay_key_secret=CHECKOUT_SECRET,
        create_order=create_order,
        create_payment_link=create_payment_link,
        policy_now=lambda: datetime(2026, 8, 24, 10, tzinfo=UTC),
    )
    app.state.created_orders = created_orders
    app.state.created_links = created_links
    with app.state.session_factory() as session:
        from app.db.tables import Customer

        session.add(Customer(customer_id="cust_dumbbell", consent=True))
        session.commit()
    return app


@pytest.mark.asyncio
async def test_storefront_exposes_dumbbell_and_creates_one_server_owned_test_order(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        storefront = await client.get("/storefront")
        first = await client.post(
            "/api/v1/orders",
            json={"idempotency_key": "dumbbell-checkout-1"},
        )
        duplicate = await client.post(
            "/api/v1/orders",
            json={"idempotency_key": "dumbbell-checkout-1"},
        )

    assert storefront.status_code == 200
    assert CHECKOUT_SECRET not in storefront.text
    assert "5 kg Dumbbell" in storefront.text
    assert "Buy Now" in storefront.text
    assert first.status_code == 201
    assert CHECKOUT_SECRET not in first.text
    assert duplicate.status_code == 200
    assert duplicate.json() == first.json()
    assert first.json() == {
        "checkout_id": first.json()["checkout_id"],
        "idempotency_key": "dumbbell-checkout-1",
        "order_id": "order_dumbbell_test",
        "obligation_reference": "order_dumbbell_test",
        "key_id": "rzp_test_local",
        "amount": 249900,
        "currency": "INR",
        "name": "ReRoute Dumbbell Store",
        "description": "5 kg Dumbbell",
        "status": "created",
    }
    assert app.state.created_orders == [(249900, "dumbbell-checkout-1")]
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(CheckoutOrder)) == 1


@pytest.mark.asyncio
async def test_order_accepts_an_idempotency_key_header_without_a_browser_payload(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/orders", headers={"Idempotency-Key": "header-only-checkout"}
        )

    assert response.status_code == 201
    assert response.json()["idempotency_key"] == "header-only-checkout"
    assert response.json()["amount"] == 249900


@pytest.mark.asyncio
async def test_checkout_callback_is_verified_but_does_not_ingest_payment(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        order = await client.post(
            "/api/v1/orders", json={"idempotency_key": "callback-checkout"}
        )
        order_id = order.json()["order_id"]
        payment_id = "pay_callback_only"
        callback = await client.post(
            "/api/v1/checkout/callback",
            json={
                "razorpay_order_id": order_id,
                "razorpay_payment_id": payment_id,
                "razorpay_signature": hmac.new(
                    CHECKOUT_SECRET.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256
                ).hexdigest(),
            },
        )
        cases = await client.get("/api/v1/cases")

    assert callback.status_code == 202
    assert callback.json() == {
        "order_id": order_id,
        "payment_id": payment_id,
        "status": "callback_received",
        "source": "checkout_callback",
    }
    assert cases.json() == []
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(PaymentEvent)) == 0
        checkout = session.scalar(
            select(CheckoutOrder).where(CheckoutOrder.provider_order_id == order_id)
        )
        assert checkout is not None
        assert checkout.status == "callback_received"


@pytest.mark.asyncio
async def test_failure_callback_is_presentation_only_and_signed_webhook_creates_the_case(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        order = await client.post(
            "/api/v1/orders", json={"idempotency_key": "failure-checkout"}
        )
        order_id = order.json()["order_id"]
        client_failure = await client.post(
            "/api/v1/checkout/failure",
            json={"order_id": order_id, "payment_id": "pay_failure_1000"},
        )
        body = webhook("payment.failed", order_id, "pay_failure_1000", "failed")
        failure = await client.post(
            "/api/v1/webhooks/razorpay",
            content=body,
            headers={"X-Razorpay-Signature": signed(body)},
        )
        cases = await client.get("/api/v1/cases")

    assert client_failure.status_code == 202
    assert client_failure.json() == {"order_id": order_id, "status": "client_failure_received"}
    assert failure.status_code == 202
    assert cases.json()[0]["case_id"] == "case_order_dumbbell_test"
    assert cases.json()[0]["obligation_reference"] == order_id
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(PaymentEvent)) == 1
        checkout = session.scalar(
            select(CheckoutOrder).where(CheckoutOrder.provider_order_id == order_id)
        )
        assert checkout is not None
        assert checkout.status == "payment_failed"
        assert checkout.payment_id == "pay_failure_1000"


@pytest.mark.asyncio
async def test_failure_duplicate_and_approved_recovery_capture_record_one_outcome(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        order = await client.post(
            "/api/v1/orders", json={"idempotency_key": "recovery-checkout"}
        )
        order_id = order.json()["order_id"]
        failure = webhook("payment.failed", order_id, "pay_failure_recovery", "failed")
        captured = webhook(
            "payment.captured",
            None,
            "pay_recovery_capture",
            "captured",
            payment_link_id="plink_dumbbell_test",
        )
        failed = await client.post(
            "/api/v1/webhooks/razorpay",
            content=failure,
            headers={"X-Razorpay-Signature": signed(failure)},
        )
        duplicate = await client.post(
            "/api/v1/webhooks/razorpay",
            content=failure,
            headers={"X-Razorpay-Signature": signed(failure)},
        )
        assert failed.status_code == 202
        assert duplicate.status_code == 200
        assert (await client.post(f"/api/v1/cases/case_{order_id}/investigate")).status_code == 200
        proposal = await client.post(
            f"/api/v1/cases/case_{order_id}/decisions",
            json={"idempotency_key": "recovery-checkout-decision"},
        )
        approved = await client.post(
            f"/api/v1/cases/case_{order_id}/decisions",
            json={
                "idempotency_key": "recovery-checkout-decision",
                "approved": True,
                "selected_action": "payment_link",
            },
            headers={"X-Reroute-Role": "business_owner"},
        )
        captured_response = await client.post(
            "/api/v1/webhooks/razorpay",
            content=captured,
            headers={"X-Razorpay-Signature": signed(captured)},
        )
        outcome = await client.get(f"/api/v1/cases/case_{order_id}/outcome")

    assert proposal.status_code == 201
    assert approved.status_code == 200
    assert approved.json()["action"]["status"] == "completed"
    assert captured_response.status_code == 202
    assert outcome.json()["outcome"]["recovered"] is True
    assert outcome.json()["outcome"]["recovered_amount"] == 249900
    assert outcome.json()["outcome"]["source"] == "razorpay_test"
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Outcome)) == 1
        assert session.scalar(select(func.count()).select_from(PaymentEvent)) == 2
        case = session.get(RecoveryCase, f"case_{order_id}")
        assert case is not None
        assert case.state == "recovered"
        assert app.state.created_links == [(249900, "recovery-checkout-decision")]


@pytest.mark.asyncio
async def test_storefront_never_allows_live_key_configuration(database_url):
    provider_calls = 0

    def provider(amount: int, idempotency_key: str) -> str:
        nonlocal provider_calls
        provider_calls += 1
        return "order_should_not_exist"

    app = create_app(
        database_url=database_url,
        razorpay_key_id="rzp_live_never",
        razorpay_key_secret="not-a-live-secret",
        create_order=provider,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/orders", json={"idempotency_key": "live-key-attempt"}
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "Razorpay Test Mode order provider is not configured"}
    assert provider_calls == 0