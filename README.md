# ReRoute Intelligence

ReRoute is a FastAPI prototype for investigating recoverable payment failures.
It ingests Razorpay Test Mode webhooks or normalized CSV events, groups failure
cohorts, ranks policy-permitted actions, and writes an audit trail for every
case transition and action attempt.

This repository contains synthetic data only. It does not send customer
messages, create live payment links, process real money, or include production
credentials.

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

Open `http://127.0.0.1:8000/` for the dashboard. The source data is
`demo/payment_events.csv`. The replay imports it, computes findings, and adds
two controlled records for the demo. One demonstrates a hard-decline retry
block. The other records the default payment-link provider failure. Both use
synthetic identifiers.

Use these commands in another terminal to inspect the demo state:

```sh
curl http://127.0.0.1:8000/api/v1/findings
curl http://127.0.0.1:8000/api/v1/cases/case_demo_hard_decline/policy
curl http://127.0.0.1:8000/api/v1/audit/case_demo_hard_decline
curl http://127.0.0.1:8000/api/v1/audit/case_demo_provider_failure
curl http://127.0.0.1:8000/api/v1/evaluations/reproducible
```

`docs/demo.md` is a five-minute walkthrough with expected observations.

## API

- `POST /api/v1/webhooks/razorpay` verifies an `X-Razorpay-Signature` against
  the raw request body before storing a normalized event.
- `POST /api/v1/data/import` imports a UTF-8 normalized CSV body.
- `POST /api/v1/findings/detect` calculates and persists ranked failure cohorts.
- `GET /api/v1/cases`, `/api/v1/audit/{case_id}`, and
  `/api/v1/cases/{case_id}/policy` expose case state, audit events, and policy.
- `POST /api/v1/cases/{case_id}/actions` executes a permitted mock action or
  attempts a Test Mode payment link. Requests need an idempotency key.
- `GET /api/v1/evaluations/reproducible` reruns the published synthetic comparison.

## Documentation

- [Five-minute demo](docs/demo.md)
- [Architecture](docs/architecture.md)
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
