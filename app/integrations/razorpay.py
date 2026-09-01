import base64
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from hashlib import sha256

RAZORPAY_TEST_KEY_PREFIX = "rzp_test_"


class PaymentLinkReference(str):
    """Customer-facing link with its provider ID retained for webhook matching."""

    provider_id: str

    def __new__(cls, value: str, provider_id: str):
        instance = super().__new__(cls, value)
        instance.provider_id = provider_id
        return instance


DUMBBELL_AMOUNT_PAISE = 249900
DUMBBELL_CURRENCY = "INR"
DUMBBELL_PRODUCT_CODE = "dumbbell_5kg"
DUMBBELL_PRODUCT_NAME = "5 kg Dumbbell"
DUMBBELL_DESCRIPTION = "5 kg Dumbbell"


def _require_test_mode_key(key_id: str, key_secret: str) -> None:
    if not key_id or not key_secret:
        raise RuntimeError("Razorpay Test Mode keys are not configured")
    if not key_id.startswith(RAZORPAY_TEST_KEY_PREFIX):
        raise RuntimeError("Razorpay Test Mode key is required")


def _basic_auth_header(key_id: str, key_secret: str) -> str:
    token = f"{key_id}:{key_secret}".encode()
    return "Basic " + base64.b64encode(token).decode()


def create_order(
    key_id: str,
    key_secret: str,
    amount: int = DUMBBELL_AMOUNT_PAISE,
    receipt: str = "reroute_dumbbell_checkout",
    timeout_seconds: int = 10,
) -> dict:
    """Create a Razorpay order using server-side Test Mode credentials.

    The storefront owns the amount and currency. This adapter keeps the API
    boundary narrow so a browser cannot choose a different price or currency.
    """
    _require_test_mode_key(key_id, key_secret)
    if amount != DUMBBELL_AMOUNT_PAISE:
        raise ValueError("unsupported storefront amount")
    url = "https://api.razorpay.com/v1/orders"
    payload = {
        "amount": amount,
        "currency": DUMBBELL_CURRENCY,
        "receipt": receipt,
        "notes": {
            "product_code": DUMBBELL_PRODUCT_CODE,
            "product_name": DUMBBELL_PRODUCT_NAME,
        },
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", _basic_auth_header(key_id, key_secret))
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            response = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        # Never return provider bodies to a browser; they can contain account data.
        raise RuntimeError("Razorpay Test Mode order request failed") from exc
    except Exception as exc:
        raise RuntimeError("Razorpay Test Mode order request failed") from exc
    if not isinstance(response, dict) or not isinstance(response.get("id"), str):
        raise RuntimeError("Razorpay Test Mode order response was invalid")
    return response


def build_order_creator(key_id: str, key_secret: str):
    """Return the narrow callable used by the server-owned checkout route."""
    _require_test_mode_key(key_id, key_secret)

    def _creator(amount: int, idempotency_key: str) -> str:
        receipt = f"reroute_{sha256(idempotency_key.encode()).hexdigest()[:24]}"
        response = create_order(
            key_id=key_id,
            key_secret=key_secret,
            amount=amount,
            receipt=receipt,
        )
        return response["id"]

    return _creator


def create_payment_link(
    key_id: str,
    key_secret: str,
    amount: int,
    reference_id: str,
    description: str | None = None,
    customer_name: str | None = None,
    customer_contact: str | None = None,
    customer_email: str | None = None,
    expire_days: int = 7,
    timeout_seconds: int = 10,
) -> dict:
    """Call Razorpay payment_links API and return the parsed response."""
    _require_test_mode_key(key_id, key_secret)

    url = "https://api.razorpay.com/v1/payment_links"
    payload: dict = {
        "amount": amount,
        "currency": "INR",
        "accept_partial": False,
        "description": description or f"Payment recovery for {reference_id}",
        "reference_id": reference_id,
        "expire_by": int(
            (datetime.now(UTC) + timedelta(days=expire_days)).timestamp()
        ),
    }
    # Only include customer block if at least one field is present
    customer: dict = {}
    if customer_name:
        customer["name"] = customer_name
    if customer_contact:
        customer["contact"] = customer_contact
    if customer_email:
        customer["email"] = customer_email
    if customer:
        payload["customer"] = customer

    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", _basic_auth_header(key_id, key_secret))

    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            body = resp.read()
            return json.loads(body)
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode() if exc.fp else str(exc)
        raise RuntimeError(f"Razorpay error {exc.code}: {err_body}") from exc
    except Exception as exc:
        raise RuntimeError(f"Razorpay request failed: {exc}") from exc


def build_payment_link_creator(key_id: str, key_secret: str):
    """Return a Callable[[int, str], str] matching app.recovery.actions expectations.

    The callable takes (amount_paise, idempotency_key) and returns the provider
    reference (payment link id or short_url). It raises on failure so the action
    layer can record action.failed.
    """

    def _creator(amount: int, reference_id: str) -> str:
        resp = create_payment_link(
            key_id=key_id,
            key_secret=key_secret,
            amount=amount,
            reference_id=reference_id,
        )
        short_url = resp.get("short_url")
        provider_id = resp.get("id")
        if isinstance(short_url, str) and isinstance(provider_id, str):
            return PaymentLinkReference(short_url, provider_id)
        # Prefer short_url for customer, fallback to id.
        return short_url or provider_id or str(resp)

    return _creator
