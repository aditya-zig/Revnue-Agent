# Five-minute demo

## Before the recording

Run the commands in the README. Keep the terminal that runs Uvicorn visible.
Open `http://127.0.0.1:8000/` in a browser. The seed replay generates the
reproducible 999-payment SyntheticCorpus with seed 47 into a new `demo.db`;
`demo/payment_events.csv` is only the small offline fallback.

## 0:00 to 0:40. Problem and evidence

Open the dashboard's Investigation view. Explain that the app looks for payment
failure cohorts with at least three events. The seeded dataset has 999 historical
PaymentEvents: 250 failures (25.03%) and 749 captures (74.97%). UPI is the
planted abnormal cohort with 225 failures in 450 attempts (50%); card is 13/275
(4.73%) and netbanking is 12/274 (4.38%).

Run:

```sh
curl http://127.0.0.1:8000/api/v1/findings
```

Point to the top finding's method cohort, support, source event identifiers,
failed value, unresolved value, recovery probability, confidence, and
recoverable impact. With seed 47, the UPI finding has support 450, failure count
225, attempted value 93,355,200 paise, impact 23,315,438 paise, and recoverable
impact 11,657,719 paise. Call the impact an estimate. It is not booked revenue
or a forecast.
Use Explain finding to explicitly save an advisory result. If no OpenRouter key
is configured, or the provider request fails, it records the deterministic
fallback; when the request succeeds, it records bounded model hypotheses and
validation steps. Refreshing the dashboard retrieves that record.

## 0:40 to 1:35. Bounded action

Run:

```sh
curl http://127.0.0.1:8000/api/v1/cases/case_order_hard_decline/policy
curl http://127.0.0.1:8000/api/v1/audit/case_order_hard_decline
```

The policy excludes `retry` because the synthetic payment has `HARD_DECLINE`.
An approved retry attempt records `action.blocked` with `hard_decline`. The
ranking code only receives permitted actions, so it cannot turn that retry back
on.

## 1:35 to 2:15. Human approval and graceful failure

The recovery queue shows the selected action and its approval pause. An action
cannot execute until a business owner approves the persisted decision. For a
provider failure, the action writes `action.failed`, assigns the case to the
business owner, and returns HTTP 502; the business owner can resume it only
after the current policy is checked again. The failure and approval records
remain in the audit trail. No real link or customer message is created.

Inspect the trace with:

```sh
curl http://127.0.0.1:8000/api/v1/audit/case_order_provider_failure
```

The default payment-link provider is intentionally absent. A manually approved
attempt against this synthetic case records `action.started` followed by
`action.failed`; the seed replay does not execute unapproved actions.

## 2:15 to 3:05. Results and limits

Open Evaluation or run:

```sh
curl http://127.0.0.1:8000/api/v1/evaluations/reproducible
```

The results rerun 30 deterministic seeds with 30 generated cases each. State
the headline accurately. Adaptive recovered 51,961,100 paise in this simulator
with zero counted safety violations. Fixed retry recovered 34,874,100 paise and
had 120 counted violations. This is simulation, not merchant revenue evidence.

## 3:05 to 4:05. Architecture

Use the RecoveryCase detail view. Follow one case through the raw event, state changes,
action attempt, and audit records. Then show `docs/architecture.md`. The key
point is ordering. Webhook or CSV input enters the database before analysis.
Policy filters actions before scoring. The action layer owns side effects and
idempotency.

## 4:05 to 5:00. Close on boundaries

Show `docs/model-limits.md` and `docs/threat-model.md`. The model trains on
generated rows. FindingAnalysis may use the optional OpenRouter adapter, but it
has no authority to bypass policy. This repository is a Test Mode and mock
prototype.
It has no authentication, real delivery provider, production secret manager,
or claim of revenue lift.
