# ReRoute Intelligence

ReRoute is a FastAPI prototype for investigating recoverable payment failures.
It ingests Razorpay Test Mode webhooks or normalized CSV events, groups failure
cohorts, ranks policy-permitted actions, and writes an audit trail for every
case transition and action attempt. Operators can also save an immutable
advisory analysis of a finding from its sanitized aggregate snapshot; OpenRouter
generation is optional and falls back to a deterministic local result.

This repository contains synthetic data only. It does not send real customer
messages or include production credentials. When local Razorpay Test Mode keys
are configured, it can create a Test Mode storefront order or approved recovery
payment link. It never handles real money.

Boundaries: the 999-payment SyntheticCorpus is SIMULATED and MOCK data from
`simulator/generator.py` with fixed seed 47. Dashboard findings, cohorts, and
the `30x30` comparison in `app/evaluation/comparison.py` are SIMULATED. Any
impact or recoverable impact is ESTIMATED, not booked revenue or a forecast.
Provider evidence is Razorpay TEST MODE only when local Test Mode keys are
configured and verified via signed `payment.failed` and `payment.captured`
webhooks in `app/api/webhooks.py`. Without Test Mode keys the app runs fully
offline on deterministic replayed Test Mode-format fixtures/signed synthetic
payloads that are not live evidence, as exercised by
`tests/integration/test_webhooks.py` and
`tests/integration/test_issue47_final_journey.py`; `scripts/live_testmode_preflight.py` verifies Test Mode readiness without printing secrets. The browser checkout at
`/storefront` requires Test Mode credentials plus a public HTTPS tunnel and a
Razorpay Dashboard webhook for `payment.failed` and `payment.captured`; a
missing key or tunnel prevents live provider calls and must not be masked.

## Run the public demo

Install [uv](https://docs.astral.sh/uv/) and Python 3.12 or later. The commands
below start with a clean, local database named `demo.db`.

```sh
git clone https://github.com/aditya-zig/Revnue-Agent.git
cd Revnue-Agent
uv sync --dev
cp .env.example .env
rm -f demo.db
REROUTE_DATABASE_URL=sqlite:///./demo.db uv run alembic upgrade head
REROUTE_DATABASE_URL=sqlite:///./demo.db uv run python scripts/seed_demo.py
REROUTE_DATABASE_URL=sqlite:///./demo.db uv run uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/` for the dashboard, or
`http://127.0.0.1:8000/storefront` for the 5 kg Dumbbell Test Mode Checkout.
The seed script generates the 999-payment SyntheticCorpus from
`simulator/generator.py`, imports it, computes findings, and adds controlled
edge cases for the demo. The committed `demo/payment_events.csv` remains a
small offline fallback. The corpus includes policy-blocked and eligible cases
with synthetic identifiers; provider actions require a persisted human approval
before they are attempted.

Use these commands in another terminal to inspect the demo state:

```sh
curl http://127.0.0.1:8000/api/v1/findings
curl http://127.0.0.1:8000/api/v1/cases/case_order_hard_decline/policy
curl http://127.0.0.1:8000/api/v1/audit/case_order_hard_decline
curl http://127.0.0.1:8000/api/v1/audit/case_order_provider_failure
curl http://127.0.0.1:8000/api/v1/evaluations/reproducible
```

`docs/demo.md` is a five-minute walkthrough with expected observations.

## API

- `GET /storefront` serves the fixed 5 kg Dumbbell product page.
- `POST /api/v1/orders` creates an idempotent server-owned Razorpay Test Mode
  order and returns only the public Checkout configuration.
- `POST /api/v1/checkout/callback` verifies the Checkout handler signature but
  does not create payment evidence; the signed webhook remains authoritative.
- `POST /api/v1/checkout/failure` accepts a browser failure notice for display
  flow only; it does not create payment evidence.
- `POST /api/v1/webhooks/razorpay` verifies an `X-Razorpay-Signature` against
  the raw request body before storing a normalized event.
- `POST /api/v1/data/import` imports a UTF-8 normalized CSV body.
- `POST /api/v1/findings/detect` calculates and persists ranked failure cohorts.
- `POST /api/v1/findings/{finding_id}/analysis` explicitly requests an OpenRouter
  `openrouter/free` advisory analysis from the sanitized snapshot; it persists
  provider metadata and returns a deterministic fallback when credentials or a
  compatible free model are unavailable, output is malformed, or the provider
  fails. Repeat requests with the same idempotency key return the original record.
- `GET /api/v1/findings/{finding_id}/analysis` and
  `GET /api/v1/finding-analyses/{analysis_id}` retrieve saved analyses without
  generating a new one.
- `GET /api/v1/cases`, `/api/v1/audit/{case_id}`, and
  `/api/v1/cases/{case_id}/policy` expose case state, audit events, and policy.
- `POST /api/v1/cases/{case_id}/investigate` moves a detected case through
  investigation and re-evaluates policy before marking it eligible.
- `POST /api/v1/cases/{case_id}/decisions` records a policy decision and the
  required human approval; approvals require the `business_owner` role.
- `POST /api/v1/cases/{case_id}/actions` executes an approved permitted mock
  action or attempts a Test Mode payment link. Requests need an idempotency key.
- `POST /api/v1/cases/{case_id}/resume` lets a business owner resume an
  escalated or awaiting-outcome case after a current policy check.
- `GET /api/v1/cases/{case_id}/outcome` exposes a persisted provider outcome
  and its audit evidence.
- `POST /api/v1/cases/{case_id}/exceptions` opens a PaymentException. Open
  exceptions block customer-directed actions until evidence resolves them.
- `GET` and owner-only `PUT /api/v1/policy-settings` expose the versioned
  quiet-hours, contact-limit, kill-switch, and mock-identity controls.
- `POST /api/v1/mock-inbox/{provider_reference}/reply` records a mock pay,
  ignore, promise, help, or opt-out reply without claiming a Test Mode payment.
- `GET /api/v1/evaluations/reproducible` reruns the published synthetic comparison.

## Razorpay tooling

Two local tools talk to Razorpay Test Mode alongside the app. They are not needed for the demo.

* **MCP server** at `https://mcp.razorpay.com/mcp`. OpenCode connects as a remote server with `Basic {env:RAZORPAY_BASIC_TOKEN}` from `~/.config/opencode/opencode.json`. See `docs/razorpay-tooling.md` for the token generation `echo -n "key_id:key_secret" | base64` and the `opencode mcp list` check.
* **CLI** `~/.local/bin/razorpay` `v1.0.9`. Installed from `https://razorpay.com/docs/api/install-cli/`, configured via `razorpay configure` to `~/.razorpay/config.yaml`. Use `razorpay payments list`, `razorpay orders create`, `razorpay payment-links list` for manual Test Mode checks.

Test keys are Test Mode only. `rzp-test-key.csv` is gitignored and should not be committed. The app reads `RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET` from the local environment only when it creates a Test Mode storefront order or recovery payment link. It rejects live-mode key IDs and uses `REROUTE_RAZORPAY_WEBHOOK_SECRET` for webhook HMAC in `app/api/webhooks.py`.

Details and troubleshooting in `docs/razorpay-tooling.md`.

## Documentation

- [Five-minute demo](docs/demo.md)
- [Architecture](docs/architecture.md)
- [Razorpay tooling](docs/razorpay-tooling.md)
- [Threat model](docs/threat-model.md)
- [Evaluation](docs/evaluation.md)
- [Model limits](docs/model-limits.md)
- [Prior art and primary references](docs/prior-art.md)

## Verify

```sh
uv run pytest
uv run ruff check .
uv run mypy
```
