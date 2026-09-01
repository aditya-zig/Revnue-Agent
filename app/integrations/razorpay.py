import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from hashlib import sha256

ORDER_PROVIDER_ERROR = "Razorpay Test Mode order creation failed"
PAYMENT_LINK_PROVIDER_ERROR = "Razorpay Test Mode payment link creation failed"


class RazorpayProviderError(RuntimeError):
    """A provider error safe to expose after its diagnostic is sanitized."""

    def __init__(self, message: str, *, diagnostic: str, uncertain: bool = False):
        super().__init__(message)
        self.diagnostic = diagnostic
        self.uncertain = uncertain


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
    idempotency_key: str | None = None,
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
    if idempotency_key:
        # Razorpay currently reconciles this request by receipt as well. Keep the
        # provider key on the request when supported so a retried POST is safe.
        req.add_header("X-Razorpay-Idempotency", idempotency_key)
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            response = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        # Never return provider bodies to a browser; they can contain account data.
        raise RazorpayProviderError(
            ORDER_PROVIDER_ERROR,
            diagnostic=f"order_http_status={exc.code}",
            uncertain=True,
        ) from exc
    except Exception as exc:
        # A timeout or malformed response may mean that the provider accepted the
        # request. The caller must reconcile the deterministic receipt before retrying.
        raise RazorpayProviderError(
            ORDER_PROVIDER_ERROR,
            diagnostic=f"order_provider_exception={type(exc).__name__}",
            uncertain=True,
        ) from exc
    if not isinstance(response, dict) or not isinstance(response.get("id"), str):
        raise RazorpayProviderError(
            ORDER_PROVIDER_ERROR,
            diagnostic="order_provider_response_invalid",
            uncertain=True,
        )
    return response


def order_receipt_for_idempotency_key(idempotency_key: str) -> str:
    """Return the bounded, deterministic provider receipt for a checkout key."""
    return f"reroute_{sha256(idempotency_key.encode()).hexdigest()[:24]}"


def _is_expected_order(order: dict, receipt: str) -> bool:
    return (
        order.get("receipt") in (None, receipt)
        and order.get("amount") in (None, DUMBBELL_AMOUNT_PAISE)
        and order.get("currency") in (None, DUMBBELL_CURRENCY)
        and isinstance(order.get("id"), str)
        and bool(order["id"])
    )


def find_order_by_receipt(
    key_id: str,
    key_secret: str,
    receipt: str,
    timeout_seconds: int = 10,
) -> dict | None:
    """Find an existing provider order without exposing its response or errors."""
    _require_test_mode_key(key_id, key_secret)
    query = urllib.parse.urlencode({"receipt": receipt})
    req = urllib.request.Request(
        f"https://api.razorpay.com/v1/orders?{query}", method="GET"
    )
    req.add_header("Authorization", _basic_auth_header(key_id, key_secret))
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            response = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise RazorpayProviderError(
            ORDER_PROVIDER_ERROR,
            diagnostic=f"order_lookup_http_status={exc.code}",
            uncertain=True,
        ) from exc
    except Exception as exc:
        raise RazorpayProviderError(
            ORDER_PROVIDER_ERROR,
            diagnostic=f"order_lookup_exception={type(exc).__name__}",
            uncertain=True,
        ) from exc

    if not isinstance(response, dict):
        raise RazorpayProviderError(
            ORDER_PROVIDER_ERROR,
            diagnostic="order_lookup_response_invalid",
            uncertain=True,
        )
    if _is_expected_order(response, receipt):
        return response
    items = response.get("items")
    if not isinstance(items, list):
        return None
    for item in items:
        if isinstance(item, dict) and _is_expected_order(item, receipt):
            return item
    return None


class RazorpayOrderCreator:
    """Callable Test Mode order creator with deterministic-receipt recovery."""

    def __init__(self, key_id: str, key_secret: str):
        _require_test_mode_key(key_id, key_secret)
        self.key_id = key_id
        self.key_secret = key_secret

    def __call__(self, amount: int, idempotency_key: str) -> str:
        response = create_order(
            key_id=self.key_id,
            key_secret=self.key_secret,
            amount=amount,
            receipt=order_receipt_for_idempotency_key(idempotency_key),
            idempotency_key=idempotency_key,
        )
        return response["id"]

    def reconcile(self, receipt: str) -> dict | None:
        return find_order_by_receipt(self.key_id, self.key_secret, receipt)


def build_order_creator(key_id: str, key_secret: str):
    """Return the narrow callable used by the server-owned checkout route."""
    return RazorpayOrderCreator(key_id, key_secret)


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
            response = json.loads(body)
    except urllib.error.HTTPError as exc:
        raise RazorpayProviderError(
            PAYMENT_LINK_PROVIDER_ERROR,
            diagnostic=f"payment_link_http_status={exc.code}",
        ) from exc
    except Exception as exc:
        raise RazorpayProviderError(
            PAYMENT_LINK_PROVIDER_ERROR,
            diagnostic=f"payment_link_provider_exception={type(exc).__name__}",
        ) from exc
    if not isinstance(response, dict):
        raise RazorpayProviderError(
            PAYMENT_LINK_PROVIDER_ERROR,
            diagnostic="payment_link_provider_response_invalid",
        )
    return response


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
        if not isinstance(provider_id, str) or not provider_id:
            raise RazorpayProviderError(
                PAYMENT_LINK_PROVIDER_ERROR,
                diagnostic="payment_link_provider_response_missing_id",
            )
        # Keep the durable provider ID even when a provider response omits its
        # customer URL. Never turn an arbitrary response body into a reference.
        return PaymentLinkReference(
            short_url if isinstance(short_url, str) and short_url else provider_id,
            provider_id,
        )

    return _creator
