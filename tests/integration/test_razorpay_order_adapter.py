import base64
import json
from email.message import Message
from io import BytesIO
from typing import cast
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from app.integrations.razorpay import (
    DUMBBELL_AMOUNT_PAISE,
    PAYMENT_LINK_PROVIDER_ERROR,
    PaymentLinkReference,
    RazorpayProviderError,
    build_payment_link_creator,
    create_order,
    create_payment_link,
    find_order_by_receipt,
)


def test_order_adapter_sends_a_test_mode_order_without_exposing_the_secret(monkeypatch):
    observed: dict[str, object] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return b'{"id":"order_test_adapter"}'

    def urlopen(request: Request, timeout: int):
        observed["url"] = request.full_url
        observed["timeout"] = timeout
        observed["payload"] = json.loads(cast(bytes, request.data or b"{}"))
        observed["authorization"] = request.get_header("Authorization")
        observed["provider_idempotency"] = dict(
            (name.lower(), value) for name, value in request.header_items()
        ).get("x-razorpay-idempotency")
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    result = create_order(
        "rzp_test_adapter",
        "local-test-secret",
        DUMBBELL_AMOUNT_PAISE,
        "reroute_adapter_receipt",
        idempotency_key="checkout-adapter-key",
    )

    assert result == {"id": "order_test_adapter"}
    assert observed["url"] == "https://api.razorpay.com/v1/orders"
    assert observed["timeout"] == 10
    assert observed["provider_idempotency"] == "checkout-adapter-key"
    assert observed["payload"] == {
        "amount": DUMBBELL_AMOUNT_PAISE,
        "currency": "INR",
        "receipt": "reroute_adapter_receipt",
        "notes": {"product_code": "dumbbell_5kg", "product_name": "5 kg Dumbbell"},
    }
    authorization = observed["authorization"]
    assert isinstance(authorization, str)
    assert base64.b64decode(authorization.removeprefix("Basic ")).decode() == (
        "rzp_test_adapter:local-test-secret"
    )
    assert "local-test-secret" not in json.dumps(result)


def test_order_adapter_reconciles_by_the_deterministic_receipt(monkeypatch):
    observed: dict[str, object] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return b'{"items":[{"id":"order_reconciled","receipt":"reroute_receipt"}]}'

    def urlopen(request: Request, timeout: int):
        observed["url"] = request.full_url
        observed["method"] = request.method
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    result = find_order_by_receipt(
        "rzp_test_adapter", "local-test-secret", "reroute_receipt"
    )

    assert result == {"id": "order_reconciled", "receipt": "reroute_receipt"}
    assert observed == {
        "url": "https://api.razorpay.com/v1/orders?receipt=reroute_receipt",
        "method": "GET",
    }


def test_payment_link_adapter_retains_the_durable_provider_id(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return b'{"id":"plink_adapter_001","short_url":"https://rzp.io/rzp/test"}'

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: Response())
    creator = build_payment_link_creator("rzp_test_adapter", "local-test-secret")

    result = creator(DUMBBELL_AMOUNT_PAISE, "link-adapter-001")

    assert isinstance(result, PaymentLinkReference)
    assert result == "https://rzp.io/rzp/test"
    assert result.provider_id == "plink_adapter_001"


def test_payment_link_adapter_rejects_success_without_a_provider_id(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return b'{"short_url":"https://rzp.io/rzp/no-id","customer":{"email":"secret"}}'

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: Response())
    creator = build_payment_link_creator("rzp_test_adapter", "local-test-secret")

    with pytest.raises(RazorpayProviderError, match=PAYMENT_LINK_PROVIDER_ERROR) as error:
        creator(DUMBBELL_AMOUNT_PAISE, "link-adapter-missing-id")

    assert "secret" not in str(error.value)
    assert error.value.diagnostic == "payment_link_provider_response_missing_id"


def test_payment_link_adapter_redacts_provider_http_body(monkeypatch):
    def urlopen(request: Request, timeout: int):
        raise HTTPError(
            request.full_url,
            400,
            "bad request",
            Message(),
            BytesIO(b'{"description":"customer-secret"}'),
        )

    monkeypatch.setattr("urllib.request.urlopen", urlopen)

    with pytest.raises(RazorpayProviderError) as error:
        create_payment_link(
            "rzp_test_adapter",
            "local-test-secret",
            DUMBBELL_AMOUNT_PAISE,
            "link-adapter-http-error",
        )

    assert str(error.value) == PAYMENT_LINK_PROVIDER_ERROR
    assert "customer-secret" not in str(error.value)
    assert error.value.diagnostic == "payment_link_http_status=400"


def test_order_adapter_rejects_live_mode_before_network(monkeypatch):
    called = False

    def urlopen(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("network must not be called")

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    with pytest.raises(RuntimeError, match="Test Mode key is required"):
        create_order("rzp_live_never", "secret", DUMBBELL_AMOUNT_PAISE, "receipt")
    assert called is False
