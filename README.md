# ReRoute Intelligence

ReRoute ingests Razorpay Test Mode payment events, opens recoverable-payment
cases, and records each state transition in an audit log. This commit provides
the Phase 1 foundation only.

## Run locally

```sh
uv sync --dev
cp .env.example .env
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

The health endpoint is available at `GET /health`. The application reads
`REROUTE_DATABASE_URL` and `REROUTE_RAZORPAY_WEBHOOK_SECRET` from `.env`.

## HTTP endpoints

- `POST /api/v1/webhooks/razorpay` verifies the `X-Razorpay-Signature` against
  the exact request body before normalizing and storing the event.
- `POST /api/v1/data/import` accepts a UTF-8 CSV body with normalized payment
  event columns.
- `GET /api/v1/cases` and `GET /api/v1/audit/{case_id}` expose the current
  case state and its append-only audit trail.
- `GET /api/v1/cases/{case_id}/policy` returns the permitted recovery actions,
  blocked-action reasons, and policy version.
- `POST /api/v1/cases/{case_id}/actions` executes a permitted mock action or a
  Test Mode payment link. The request includes an action and idempotency key.

## Limits

This project uses synthetic data and Razorpay Test Mode only. It does not send
customer messages. The default payment-link provider rejects requests until an
application injects a Test Mode provider.
