import pytest

from scripts.genuine_testmode_session import (
    prove_provider_order,
    validate_simulation,
)


def test_validate_simulation_accepts_expected_history():
    validate_simulation(
        {
            "payments_total": 999,
            "successes": 749,
            "failures": 250,
            "findings": 37,
        }
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("payments_total", 998),
        ("successes", 748),
        ("failures", 249),
        ("findings", 36),
    ],
)
def test_validate_simulation_rejects_wrong_history(field, value):
    payload = {
        "payments_total": 999,
        "successes": 749,
        "failures": 250,
        "findings": 37,
    }
    payload[field] = value
    with pytest.raises(RuntimeError, match=f"simulation_{field}_invalid"):
        validate_simulation(payload)


def test_prove_provider_order_uses_exact_provider_fetch(monkeypatch):
    monkeypatch.setattr(
        "scripts.genuine_testmode_session.request_json",
        lambda *args, **kwargs: {
            "order_id": "order_probe_001",
            "amount": 249900,
            "currency": "INR",
        },
    )
    monkeypatch.setattr(
        "scripts.genuine_testmode_session.fetch_order_by_id",
        lambda *args, **kwargs: {
            "id": "order_probe_001",
            "receipt": "genuine-session-order-receipt",
            "amount": 249900,
            "currency": "INR",
        },
    )
    monkeypatch.setattr(
        "scripts.genuine_testmode_session.order_receipt_for_idempotency_key",
        lambda key: "genuine-session-order-receipt",
    )
    monkeypatch.setattr(
        "scripts.genuine_testmode_session.wait_for_receipt_reconciliation",
        lambda *args: (None, 6),
    )
    result = prove_provider_order(
        base_url="http://127.0.0.1:8000",
        key_id="rzp_test_example",
        key_secret="example-secret",
    )
    assert result["direct_fetch_verified"] is True
    assert result["provider_round_trip"] is True
