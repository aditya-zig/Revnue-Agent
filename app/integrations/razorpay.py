import base64
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta


def _basic_auth_header(key_id: str, key_secret: str) -> str:
    token = f"{key_id}:{key_secret}".encode()
    return "Basic " + base64.b64encode(token).decode()


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
    if not key_id or not key_secret:
        raise RuntimeError("Razorpay keys not configured")

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
        # Prefer short_url for customer, fallback to id
        return resp.get("short_url") or resp.get("id") or str(resp)

    return _creator
