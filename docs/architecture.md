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

The webhook or CSV is the only input the app trusts. Two operator tools sit alongside that flow and are not part of it. See `docs/razorpay-tooling.md`.

* **MCP server** `https://mcp.razorpay.com/mcp`. Hosted remote, streamable HTTP. OpenCode connects from `~/.config/opencode/opencode.json` as a `remote` server with `Authorization: Basic {env:RAZORPAY_BASIC_TOKEN}`. It exposes about 42 tools like `create_order` and `create_payment_link` against the same Test Mode account the CLI uses. The app does not call the MCP itself.
* **CLI** `~/.local/bin/razorpay` `v1.0.9`. Installed from the Razorpay docs, configured to `~/.razorpay/config.yaml` via `razorpay configure`. Use it for manual `razorpay payments list` or `razorpay orders create` checks. There is no `razorpay webhook` subcommand, so local webhook tests use a signed `curl` to `POST /api/v1/webhooks/razorpay`.

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
