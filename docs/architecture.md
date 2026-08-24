# Architecture

ReRoute has one FastAPI process and one SQLite database in the local demo. The
same process owns ingestion, analysis, policy, action execution, and the
dashboard. There is no queue, background worker, or external model service.

```text
Razorpay Test Mode webhook or normalized CSV
  -> ingestion and deduplication
  -> payment event and recovery case
  -> leak detector and ranked finding
  -> policy filter
  -> recovery scorer and structured decision
  -> mock or Test Mode action
  -> audit event, outcome, dashboard
```

`app/api/webhooks.py` verifies the HMAC before parsing a webhook. It stores the
raw body and normalized fields, then calls `record_event_and_update_case`.
`app/ingestion/csv_loader.py` uses the same normalized event path for the demo
CSV.

`app/leak_analysis/detector.py` groups payment events by error, method, amount,
time, and customer history. A finding needs at least three supporting events.
It stores source event identifiers, support, failure count, attempted value,
unresolved value, and the recovery probability used in the estimate.

`app/policy/evaluate.py` determines which actions are allowed. The recovery
model ranks only that list. `app/recovery/controller.py` rejects malformed or
policy-blocked model output and selects the highest-ranked allowed action when
no model function is present. `app/recovery/actions.py` enforces idempotency,
records actions and audit events, and turns provider errors into HTTP 502.

The database tables are defined in `app/db/tables.py`. They retain customers,
payment events, recovery cases, findings, decisions, actions, outcomes, and
append-only audit events. The dashboard returns estimates, synthetic simulation
results, and Test Mode outcomes as separate values.
