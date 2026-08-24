# Five-minute demo

## Before the recording

Run the commands in the README. Keep the terminal that runs Uvicorn visible.
Open `http://127.0.0.1:8000/` in a browser. The replay uses only
`demo/payment_events.csv` and a new `demo.db`.

## 0:00 to 0:40. Problem and evidence

Open the dashboard's Investigation view. Explain that the app looks for payment
failure cohorts with at least three events. The seeded dataset has three UPI
failures with `insufficient funds` and three small successful card payments.

Run:

```sh
curl http://127.0.0.1:8000/api/v1/findings
```

Point to the finding's cohort filter, support, source event identifiers, failed
value, unresolved value, recovery probability, confidence, and recoverable
impact. Call the impact an estimate. It is not booked revenue or a forecast.

## 0:40 to 1:35. Bounded action

Run:

```sh
curl http://127.0.0.1:8000/api/v1/cases/case_demo_hard_decline/policy
curl http://127.0.0.1:8000/api/v1/audit/case_demo_hard_decline
```

The policy excludes `retry` because the synthetic payment has `HARD_DECLINE`.
The audit trail contains `action.blocked` with `hard_decline`. The ranking code
only receives permitted actions, so it cannot turn that retry back on.

## 1:35 to 2:15. Graceful failure

Run:

```sh
curl http://127.0.0.1:8000/api/v1/audit/case_demo_provider_failure
```

The default payment-link provider is intentionally absent. The replay attempts
that allowed action against a synthetic case, records `action.started` followed
by `action.failed`, and leaves the error in the audit trail. No real link or
customer message was created.

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

Use the Timeline view. Follow one case through the raw event, state changes,
action attempt, and audit records. Then show `docs/architecture.md`. The key
point is ordering. Webhook or CSV input enters the database before analysis.
Policy filters actions before scoring. The action layer owns side effects and
idempotency.

## 4:05 to 5:00. Close on boundaries

Show `docs/model-limits.md` and `docs/threat-model.md`. The model trains on
generated rows. It has no production data, no external model service, and no
authority to bypass policy. This repository is a Test Mode and mock prototype.
It has no authentication, real delivery provider, production secret manager,
or claim of revenue lift.
