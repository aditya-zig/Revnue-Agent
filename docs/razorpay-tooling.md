# Razorpay tooling

ReRoute uses Razorpay Test Mode for the dumbbell storefront, webhooks, and approved recovery payment links. Two local tools talk to Razorpay without changing the app: the remote MCP server for the AI agent, and the CLI for manual checks. All three use the same Test Mode key pair. `app/core/config.py` reads the keys from `.env` via `RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET`, and checks webhook signatures with `RAZORPAY_WEBHOOK_SECRET`. The app rejects non-`rzp_test_` keys before making a provider request.

## Keys

Test keys look like `rzp_test_...` for the ID. The secret is the paired value. Generate a merchant token for the MCP with:

```sh
echo -n "rzp_test_...:your_secret" | base64
```

The output is the value for `RAZORPAY_BASIC_TOKEN`. It is an opaque base64 of `key_id:key_secret`, not the keys themselves.

Storage:

* `.env` holds `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, and `RAZORPAY_WEBHOOK_SECRET` for the app. `app/core/config.py` also accepts `REROUTE_` prefixed forms.
* `RAZORPAY_BASIC_TOKEN` lives in the shell, exported from `~/.zshrc` and `~/.bashrc`. `~/.config/opencode/opencode.json` currently hardcodes the Basic token after the GUI fix, but also supports `Basic {env:RAZORPAY_BASIC_TOKEN}`.
* `~/.razorpay/config.yaml` holds `key_id` and `key_secret` for the CLI, written by `razorpay configure`.
* `rzp-test-key.csv` is a one-time import file. It is listed in `.gitignore` and should not be committed. The copy that was in the repo root has been removed. Keep test keys out of git.

All keys here are Test Mode. They cannot move money.

## MCP server

* URL: `https://mcp.razorpay.com/mcp`. Streamable HTTP. The older `/sse` endpoint was deprecated on 2025-08-13.
* Type: hosted remote, zero infra. OpenCode connects as a `remote` MCP with a `Basic` header, no `mcp-remote` bridge needed.
* Config: `~/.config/opencode/opencode.json` and `~/.config/opencode/opencode.jsonc` both contain:

```json
"razorpay": {
  "type": "remote",
  "url": "https://mcp.razorpay.com/mcp",
  "enabled": true,
  "headers": {
    "Authorization": "Basic {env:RAZORPAY_BASIC_TOKEN}"
  }
}
```

* Tools: 42 tools at last check, including `create_order`, `capture_payment`, `create_payment_link`, `create_qr_code`. The agent calls them directly. You do not copy curl by hand.
* Scope: same Test Mode account as the CLI. Creating an order via MCP shows up in `razorpay orders list`.

Verify:

```sh
export RAZORPAY_BASIC_TOKEN="$(echo -n "rzp_test_...:..." | base64)"
opencode mcp list
# expect razorpay connected, not needs authentication

curl -s -X POST https://mcp.razorpay.com/mcp \
  -H "Authorization: Basic $RAZORPAY_BASIC_TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | head -c 1000
```

If opencode still says `needs authentication`, the env var was not in the process that launched it. Restart the terminal or run `source ~/.zshrc` first.

## CLI

* Install: `curl -fsSL https://razorpay.com/cli/latest/install.sh | bash` puts `razorpay` in `~/.local/bin/razorpay`, currently `v1.0.9`. Install script from https://razorpay.com/docs/api/install-cli/.
* Configure:

```sh
razorpay configure --key-id rzp_test_... --key-secret ...
# writes ~/.razorpay/config.yaml
razorpay --version
razorpay payments list --count 2
```

* Common commands for this project:

```sh
razorpay payments list --count 5
razorpay orders list --count 5
razorpay orders create --amount 50000 --currency INR --receipt reroute_test_1
razorpay orders fetch order_TUVstBfhnBrlUW
razorpay payment-links list
```

Orders and payment links created here are Test Mode objects. ReRoute stores its own `PaymentEvent` and `RecoveryCase` rows separately. It does not auto-sync Razorpay dashboard state except through webhooks you send to `POST /api/v1/webhooks/razorpay`.

* No `razorpay webhook` subcommand exists in `v1.0.9`. To exercise the webhook path locally, send a signed request yourself:

```sh
PAYLOAD='{"entity":"event","event":"payment.failed","payload":{"payment":{"id":"pay_test123","amount":50000,"currency":"INR","status":"failed"}}}'
SIG=$(echo -n "$PAYLOAD" | openssl dgst -sha256 -hmac "$REROUTE_RAZORPAY_WEBHOOK_SECRET" | cut -d' ' -f2)
curl -X POST http://127.0.0.1:8000/api/v1/webhooks/razorpay \
  -H "Content-Type: application/json" \
  -H "X-Razorpay-Signature: $SIG" \
  -d "$PAYLOAD"
```

The HMAC logic is in `app/core/security.py` and the route in `app/api/webhooks.py`.

## Configure your project

Add keys to `.env`. Both forms work because `app/core/config.py:7` accepts either prefix.

```bash
# .env
RAZORPAY_KEY_ID=rzp_test_1234567890abcd
RAZORPAY_KEY_SECRET=abcDEF1234567890xyz
RAZORPAY_WEBHOOK_SECRET=webhook_secret_1234567890
# or with REROUTE_ prefix, same values
REROUTE_RAZORPAY_KEY_ID=rzp_test_1234567890abcd
REROUTE_RAZORPAY_KEY_SECRET=abcDEF1234567890xyz
REROUTE_RAZORPAY_WEBHOOK_SECRET=webhook_secret_1234567890
```

`app/core/config.py:4` maps them:

```python
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    razorpay_key_id: str = Field(default="", validation_alias=AliasChoices("REROUTE_RAZORPAY_KEY_ID", "RAZORPAY_KEY_ID"))
    razorpay_key_secret: str = Field(default="", validation_alias=AliasChoices("REROUTE_RAZORPAY_KEY_SECRET", "RAZORPAY_KEY_SECRET"))
    razorpay_webhook_secret: str = Field(default="", validation_alias=AliasChoices("REROUTE_RAZORPAY_WEBHOOK_SECRET", "RAZORPAY_WEBHOOK_SECRET"))
    model_config = SettingsConfigDict(env_file=".env", env_prefix="REROUTE_", populate_by_name=True)
```

`app/main.py:22` builds the real creator if keys are present:

```python
from app.integrations.razorpay import build_payment_link_creator
creator = build_payment_link_creator(settings.razorpay_key_id, settings.razorpay_key_secret)
app.state.create_payment_link = creator  # used by app/recovery/actions.py:107
```

If keys are empty, it falls back to `payment link provider is not configured` and the demo stays on mock links.

## Set up webhook

Razorpay needs to POST to your `POST /api/v1/webhooks/razorpay` when a payment fails.

In the dashboard:

1. Go to `Settings → Webhooks`
2. Click `Add New Webhook`
3. URL: `https://your-server.com/api/v1/webhooks/razorpay`
4. Events: check `payment.failed` and `payment.captured`
5. Copy the webhook secret it generates and paste into `.env` as `RAZORPAY_WEBHOOK_SECRET`

For local hackathon testing, expose `http://127.0.0.1:8000`:

```bash
# Terminal 1: app
REROUTE_DATABASE_URL=sqlite:///./demo.db uv run uvicorn app.main:app --reload --port 8000

# Terminal 2: tunnel
ngrok http 8000
# Output: https://abc123def456.ngrok.io

# Use in Razorpay: https://abc123def456.ngrok.io/api/v1/webhooks/razorpay
```

`app/api/webhooks.py:24` verifies `X-Razorpay-Signature` against the raw body and `settings.razorpay_webhook_secret` before it stores anything.

## Create a storefront order and open Checkout

The customer-facing demo is available at `http://127.0.0.1:8000/storefront`. The
server owns the fixed 5 kg Dumbbell amount (₹2,499 / `249900` paise), creates a
Razorpay Test Mode order at `POST /api/v1/orders`, and returns only the public
Checkout key, order ID, amount, currency, and product description. A client
idempotency key prevents a double-click from creating a second local order or
provider order. Checkout.js receives no key secret.

The browser success handler posts the signed Checkout response to
`POST /api/v1/checkout/callback` for server-side verification only. It does not
create a `PaymentEvent` or `Outcome`. The browser `payment.failed` callback is
also presentation-only; ReRoute creates the failure `PaymentEvent` and
`RecoveryCase` only after the signed `payment.failed` webhook reaches
`POST /api/v1/webhooks/razorpay`.

A real Test Mode run requires the external setup below: Test Mode API keys, a
public HTTPS tunnel, and the Test Mode webhook configured to send
`payment.failed` and (for the later recovery capture) `payment.captured`.

## Create a payment link

`app/integrations/razorpay.py:14` posts to `https://api.razorpay.com/v1/payment_links` with Basic auth:

```python
payload = {
    "amount": amount,  # paise, so 500 INR = 50000
    "currency": "INR",
    "accept_partial": False,
    "description": f"Payment recovery for {reference_id}",
    "reference_id": reference_id,  # case_id for idempotency
    "expire_by": int((datetime.now(timezone.utc) + timedelta(days=7)).timestamp()),
    "notify": {"sms": True, "email": True},
}
```

Via `app/recovery/actions.py:107` the `payment_link` action calls it with `(case.amount_at_risk, idempotency_key)`. It returns `short_url` like `https://rzp.io/rzp/...` or the link `id`. On failure it raises and the action is recorded as `action.failed` with `502`.

You can also create one directly via MCP or CLI without touching code. Within
ReRoute, a persisted decision and business-owner approval are required before
the action endpoint attempts the provider call.

## Test it

In dashboard Test Mode:

1. Go to `Test → Payment Links`, create a manual link
2. Open the `short_url` in a browser
3. Pay with test card `4111111111111111`, any future expiry, any CVV, then choose `Fail`
4. Watch `POST /api/v1/webhooks/razorpay` receive `payment.failed`

Or via your app after you set keys:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/cases/case_demo_hard_decline/actions \
  -H "Content-Type: application/json" \
  -d '{"action":"payment_link","idempotency_key":"test_001"}'
# check audit: curl http://127.0.0.1:8000/api/v1/audit/case_demo_hard_decline
```

The direct action call is gated with `approval_required` until a persisted
decision is approved by a business owner. If keys are empty after approval,
you will get `payment link provider is not configured`. That is the mock path
the demo uses.

## Webhook payload

When a payment fails, Razorpay posts:

```json
{
  "entity": "event",
  "event": "payment.failed",
  "payload": {
    "payment": {
      "entity": "payment",
      "id": "pay_1234567890",
      "amount": 50000,
      "currency": "INR",
      "method": "upi",
      "status": "failed",
      "error_code": "BAD_REQUEST_ERROR",
      "error_description": "Insufficient funds"
    }
  }
}
```

`app/api/webhooks.py:34` parses it via `NormalizedPaymentEvent.from_razorpay` and calls `record_event_and_update_case`. It returns `401` for bad signature and `422` for bad shape.

## How this fits ReRoute

```
CLI or dashboard creates Test Mode payment
  -> Razorpay sends webhook (or you curl it)
  -> app/api/webhooks.py verifies HMAC, stores raw body
  -> ingestion normalizes to PaymentEvent
  -> detector, policy, recovery scorer
  -> persisted decision and business-owner approval
  -> action: mock or Test Mode payment_link via provider callback
  -> audit events and dashboard

MCP and CLI sit alongside that flow, not in it. They are the operator console.
Use MCP when you want the agent to create or inspect Razorpay objects.
Use CLI when you want to type it yourself.
```

See `docs/architecture.md` for the core flow and `README.md` for the five-minute demo that runs without either tool.

## Full flow example

```
1. Customer tries to pay 500 INR
   -> fails with insufficient funds

2. Razorpay POSTs to POST /api/v1/webhooks/razorpay
   -> app/api/webhooks.py verifies HMAC and creates RecoveryCase

3. Leak detector runs
   -> groups as UPI cohort, needs 3 events, computes recoverable impact

4. Policy checks app/policy/evaluate.py
   -> soft failure, payment_link is allowed

5. Ranker scores allowed actions
   -> expected value 0.40 * 500 - 25 = 175 INR

6. Business owner approves the persisted payment_link decision
   -> app/recovery/actions.py executes the approved action
   -> calls app/integrations/razorpay.py with amount 50000 and reference_id case_123

7. Razorpay returns link
   -> short_url https://rzp.io/rzp/... stored as provider_reference

8. Audit writes action.completed and case moves to AWAITING_OUTCOME. The
   provider payment-link ID is persisted alongside its customer-facing URL so
   a later capture can be correlated to the same PaymentObligation.

9. Customer clicks and pays
   -> Razorpay fires payment.captured to the same webhook

10. Webhook records outcome
    -> case marked Recovered, Actual Recovered sums it
```

## Quick reference

`.env` for the app:

```bash
RAZORPAY_KEY_ID=rzp_test_1234567890abcd
RAZORPAY_KEY_SECRET=abcDEF1234567890xyz
RAZORPAY_WEBHOOK_SECRET=webhook_secret_xyz
```

`.env.example` shows placeholders and never holds real secrets. `app/core/config.py` reads either `RAZORPAY_` or `REROUTE_RAZORPAY_` via alias.

## Checklist

- Sign up at https://razorpay.com, stay on Test
- Settings → API Keys → copy Key ID and Secret
- Add both to `.env` and to `~/.razorpay/config.yaml` via `razorpay configure`
- Base64 token for MCP: `echo -n "key_id:key_secret" | base64` → `RAZORPAY_BASIC_TOKEN`
- Settings → Webhooks → Add `https://your-server.com/api/v1/webhooks/razorpay` with `payment.failed`, `payment.captured` → copy secret to `.env`
- For local: `ngrok http 8000` and use the ngrok URL in the webhook
- Create a test payment link in dashboard, pay with `4111111111111111`, fail it, check webhook
- Verify `curl http://127.0.0.1:8000/api/v1/findings` and `curl http://127.0.0.1:8000/api/v1/audit/case_demo_hard_decline` still work

## Useful links

* Razorpay API Docs: https://razorpay.com/docs/api/
* Payment Links: https://razorpay.com/docs/payments/payment-link/
* Webhooks: https://razorpay.com/docs/webhooks/
* Test Cards: https://razorpay.com/docs/payments/test-card-details/
* ngrok: https://ngrok.com/

## Troubleshooting

* `opencode mcp list` says `needs authentication`. The env var is missing. Check `echo $RAZORPAY_BASIC_TOKEN` and that you exported before launching opencode.
* `razorpay payments list` returns `authentication failed`. Re-run `razorpay configure` with the correct Test Mode pair.
* `invalid webhook signature` from the app. The `SIG` must be HMAC of the exact raw body bytes with `REROUTE_RAZORPAY_WEBHOOK_SECRET`. Do not pretty-print the JSON before signing.
* `~/.razorpay/config.yaml` and `RAZORPAY_BASIC_TOKEN` disagree. They should be derived from the same `key_id:key_secret`. Regenerate the base64 after any `razorpay configure` change.
