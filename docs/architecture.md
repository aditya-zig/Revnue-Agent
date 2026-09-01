# Architecture

ReRoute has one FastAPI process and one SQLite database in the local demo. The
same process owns ingestion, analysis, policy, action execution, and the
dashboard. There is no queue or background worker. FindingAnalysis may call the
optional OpenRouter adapter, but it is isolated from policy and action paths.

```text
Dumbbell storefront -> server-owned Razorpay Test Mode order -> Checkout.js
  -> signed Razorpay Test Mode webhook or normalized CSV
  -> ingestion and deduplication
  -> payment event and recovery case
  -> leak detector and ranked finding
  -> explicit operator request and immutable advisory finding analysis
  -> policy filter
  -> recovery scorer and structured decision
  -> persisted human approval
  -> mock or Test Mode action
  -> signed payment.captured webhook
  -> audit event, persisted outcome, dashboard
```

The webhook or CSV is the only input the app trusts. Two operator tools sit alongside that flow and are not part of it. See `docs/razorpay-tooling.md`.

* **MCP server** `https://mcp.razorpay.com/mcp`. Hosted remote, streamable HTTP. OpenCode connects from `~/.config/opencode/opencode.json` as a `remote` server with `Authorization: Basic {env:RAZORPAY_BASIC_TOKEN}`. It exposes about 42 tools like `create_order` and `create_payment_link` against the same Test Mode account the CLI uses. The app does not call the MCP itself.
* **CLI** `~/.local/bin/razorpay` `v1.0.9`. Installed from the Razorpay docs, configured to `~/.razorpay/config.yaml` via `razorpay configure`. Use it for manual `razorpay payments list` or `razorpay orders create` checks. There is no `razorpay webhook` subcommand, so local webhook tests use a signed `curl` to `POST /api/v1/webhooks/razorpay`.

The storefront at `/storefront` owns a fixed 5 kg Dumbbell product. Its
server-only order endpoint persists an idempotent `CheckoutOrder`, returns only
Test Mode Checkout configuration, and never trusts a browser callback as
payment evidence. `app/api/webhooks.py` verifies the HMAC before parsing a
webhook. It stores the raw body and normalized fields, then calls
`record_event_and_update_case`. Recovery payment-link captures can resolve via
the persisted provider-reference mapping before entering the same state machine.
`app/ingestion/csv_loader.py` uses the same normalized event path for the demo
CSV.

`app/leak_analysis/detector.py` groups payment events by error, method, amount,
time, and customer history. A finding needs at least three supporting events.
It stores source event identifiers, support, failure count, attempted value,
unresolved value, and the recovery probability used in the estimate.

`app/finding_analysis.py` creates an analysis only for an explicit operator
request. It stores a sanitized aggregate snapshot and either a strict,
400-token-capped OpenRouter result or a deterministic fallback. Application
observed facts remain separate from model hypotheses and next validation steps;
no tools are sent and provider collection is denied. The analysis keeps the
finding identifier as provenance only, so detector runs may replace
`LeakFinding` rows without invalidating saved records; retrieval endpoints never
generate analyses.

`app/policy/evaluate.py` determines which actions are allowed. The recovery
model ranks only that list. `app/recovery/controller.py` rejects malformed or
policy-blocked model output and selects the highest-ranked allowed action when
no model function is present. `app/recovery/actions.py` enforces persisted
action-specific approval, idempotency, and audit events, and turns provider
errors into HTTP 502. A business owner can resume an escalated or
awaiting-outcome case after the current policy is checked again. Signed Test
Mode captures are provider-authoritative and create the persisted Outcome used
by the dashboard.

The database tables are defined in `app/db/tables.py`. They retain customers,
payment events, recovery cases, findings, immutable finding analyses, decisions,
actions, outcomes, and append-only audit events. The dashboard returns
estimates, saved finding analyses, synthetic simulation results, and Test Mode
outcomes as separate values.
