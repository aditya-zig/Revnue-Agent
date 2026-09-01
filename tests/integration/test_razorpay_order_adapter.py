import base64
import json
from typing import cast
from urllib.request import Request

import pytest

from app.integrations.razorpay import DUMBBELL_AMOUNT_PAISE, create_order


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
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    result = create_order(
        "rzp_test_adapter",
        "local-test-secret",
        DUMBBELL_AMOUNT_PAISE,
        "reroute_adapter_receipt",
    )

    assert result == {"id": "order_test_adapter"}
    assert observed["url"] == "https://api.razorpay.com/v1/orders"
    assert observed["timeout"] == 10
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
