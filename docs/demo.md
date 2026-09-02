# Five-minute demo

## Before the recording

Run the commands in the README. Keep the terminal that runs Uvicorn visible.
Open `http://127.0.0.1:8000/` in a browser. The seed replay generates the
reproducible 999-payment SyntheticCorpus with seed 47 into a new `demo.db`;
`demo/payment_events.csv` is only the small offline fallback.

For the Issue #47 failure-first path, open `http://127.0.0.1:8000/storefront`
in a separate tab. It is a fixed 5 kg Dumbbell at ₹2,499 and uses only
Razorpay Test Mode. Click Buy Now once, choose the official Test Mode failure
path in Checkout, and treat the browser error as presentation only. Verify the
signed `payment.failed` webhook creates the order-linked `PaymentEvent` and
`RecoveryCase`; a duplicate delivery must not create another event. Continue
through the existing investigation, Policy, decision, business-owner approval,
and recovery action flow. A later signed capture from that recovery action is
the only source for the persisted `razorpay_test` Outcome.

This browser portion has an external boundary: it requires Test Mode API keys,
a public HTTPS tunnel, and a Test Mode Razorpay webhook configured for
`payment.failed` and `payment.captured`. Local tests use deterministic provider
doubles and signed synthetic payloads; they never call Razorpay. An offline signed fixture is not live provider evidence.

## 5:00 timed map — 9 beats (read during recording)

Keep every claim inside its provenance. The narrator must distinguish `ESTIMATED`, `TEST MODE`, `SIMULATED`, and `MOCK` and make no production-revenue claim.

- **0:00–0:30 problem / ReRoute.** One line on the leak problem: isolated failures hide cohort-level revenue risk. Show the flow `Payments → detect abnormal failure cohorts → estimate recoverable loss → policy filters actions → choose recovery action → track outcome`. Name the four operator questions: where is money leaking, can we safely recover it, what should ReRoute do, did it work.

- **0:30–0:50 exactly 999 `SIMULATED` / `MOCK` history.** Run `REROUTE_DATABASE_URL=sqlite:///./demo.db uv run python scripts/seed_demo.py`. State it is exactly 999 `SIMULATED`/`MOCK` payments from `simulator/generator.py` seed 47, not live merchant data. Seeded shape: 999 events, 250 failures (25.03%) and 749 captures, UPI 225/450 (50%), card 13/275 (4.73%), netbanking 12/274 (4.38%). (Dashboard **Simulate 999 Payments** is future Issue #47 intent, not an existing control.)

- **0:50–1:30 dumbbell and #1000 failure-first event.** Open `/storefront`. Show the 5 kg Dumbbell at ₹2,499, click **Buy Now**, open official Razorpay Test Mode Checkout, deliberately trigger the supported Test Mode failure. Note the browser error is presentation only; the backend source of truth is the signed `payment.failed` webhook that creates the order-linked `PaymentEvent` and `RecoveryCase` with deduplication.

- **1:30–2:10 1,000-payment dashboard.** Open the dashboard with the full 1,000-payment population (999 history plus #1000). Point to total payments, successes versus failures, baseline failure rate, value at risk, and abnormal cohorts. Make the connection explicit: the live failure can be compared against the full population to decide if it belongs to a larger leak.

- **2:10–2:50 finding / cohort and `ESTIMATED` impact.** Open the top finding. Run `curl http://127.0.0.1:8000/api/v1/findings` and read method cohort, support, source event identifiers, failed value, unresolved value, recovery probability, confidence, and recoverable impact. With seed 47 the UPI finding has support 450, failure count 225, attempted value 93,355,200 paise, impact 23,315,438 paise, recoverable impact 11,657,719 paise. Call every impact `ESTIMATED`, not booked revenue or a forecast. Optionally run `Explain finding` to save an advisory `FindingAnalysis` from the sanitized snapshot; it records a deterministic fallback when no free OpenRouter key or model is available.

- **2:50–3:30 policy / ranking.** Explain deterministic policy decides which actions are allowed and the model only ranks permitted actions. Run `curl http://127.0.0.1:8000/api/v1/cases/case_order_hard_decline/policy` and `curl http://127.0.0.1:8000/api/v1/audit/case_order_hard_decline` to show policy and audit evidence. Follow one case `DETECTED → INVESTIGATED → ELIGIBLE → ACTION_SELECTED → AWAITING_OUTCOME` and show `docs/architecture.md` ordering: webhook or CSV enters the database before analysis, policy filters before scoring, the action layer owns side effects and idempotency.

- **3:30–3:55 hard-decline retry safety.** Open the hard-decline case. Show policy excludes `retry` because the synthetic payment has `HARD_DECLINE`; an approved retry attempt records `action.blocked` with `hard_decline`. Ranking receives only permitted actions so it cannot re-enable the retry. This proves policy cannot be bypassed.

- **3:55–4:35 approved recovery and `TEST MODE` / `RECOVERED` outcome.** Show the recovery queue approval pause: no action executes until a `business_owner` approves the persisted decision. Run `curl http://127.0.0.1:8000/api/v1/audit/case_order_provider_failure` to inspect a failure trace where the default payment-link provider is intentionally absent and records `action.started` then `action.failed` with HTTP 502 and escalation. Then follow the approved path: resumed case, signed `payment.captured` webhook from the recovery payment link, persisted `razorpay_test` Outcome, `AWAITING_OUTCOME → RECOVERED`, cancelled pending actions, and full audit trail. No real link or customer message is created.

- **4:35–5:00 `SIMULATED` evaluation.** Open Evaluation or run `curl http://127.0.0.1:8000/api/v1/evaluations/reproducible`. State accurately: 30 deterministic seeds with 30 generated cases each, Adaptive recovered 51,961,100 paise with zero counted safety violations, Fixed retry recovered 34,874,100 paise with 120 counted violations. Label this `SIMULATED`. This is simulation, not merchant revenue evidence. Close on `docs/model-limits.md` and `docs/threat-model.md`: model trains on generated rows, FindingAnalysis has no authority to bypass policy, this repo has no authentication, real delivery provider, production secret manager, or claim of revenue lift.

## Recording commands

Keep these exact commands visible during the beats above:

```sh
curl http://127.0.0.1:8000/api/v1/findings
curl http://127.0.0.1:8000/api/v1/cases/case_order_hard_decline/policy
curl http://127.0.0.1:8000/api/v1/audit/case_order_hard_decline
curl http://127.0.0.1:8000/api/v1/audit/case_order_provider_failure
curl http://127.0.0.1:8000/api/v1/evaluations/reproducible
```
