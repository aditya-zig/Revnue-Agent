# Razorpay UX handoff

## Source of truth

The dashboard rebuild follows `/home/batman/Projects/code/revnue-agent/Razorpay-Design-System.zip`.
The implementation uses the archive's white, cloud, navy, slate blue, cool grey, Razorpay blue,
and deep blue palette, 8px spacing rhythm, 8px control radius, local Inter font files, and
the supplied Razorpay lockup.

## Implemented surface

`app/api/dashboard.py` now serves a responsive product surface with these views:

- Overview with business metrics and a case in focus
- Recovery queue with policy-backed approval controls
- RecoveryCase detail with the persisted execution trace
- PaymentExceptions
- Policy settings
- Investigation
- Evaluation
- Mock inbox within case detail

The FastAPI app serves local assets at `/static` from `app/static`.

## Rs. 2,499 journey

The first persisted case is the overview story. The UI shows its amount at risk, failed provider
evidence, recorded policy decision, recovery action, audit records, and provider outcome in API
order. Approval controls keep the existing action endpoint and idempotency behavior. The UI does
not manufacture trace steps or recovery results.

## Evidence boundaries

Executive values retain their existing labels and sources. Estimated values come from persisted
leak findings, Test Mode recovery comes from recorded outcomes, and evaluation values remain
SIMULATED. Empty, blocked, failed, loading, and unavailable-data states are rendered explicitly.

## Validation

- `uv run pytest -q`: 55 passed
- `uv run mypy`: passed
- `uv run ruff check .`: existing E501 failures remain in migration and seed files
- `chrome-devtools-axi` visual capture was unavailable because the installed wrapper only looked
  for Chrome at `/opt/google/chrome/chrome`; Chromium is installed at `/usr/bin/chromium`.
