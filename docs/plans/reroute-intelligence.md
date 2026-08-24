# ReRoute Intelligence implementation plan

This document records the plan imported from the supplied Notion export on 2026-08-24. The GitHub build spec and its tickets are the working source for implementation. Keep this document as the product brief and update the spec or tickets when decisions change.

## Product

ReRoute Intelligence finds recoverable payment-failure revenue, explains the evidence, selects a permitted recovery action, records its result, and compares that policy with a fixed retry schedule.

The target user is a small Indian subscription merchant. Demonstrations use synthetic data and Razorpay Test Mode only.

## Core flow

1. Import seeded customer and payment data.
2. Receive and verify Razorpay Test Mode payment events.
3. Find the cohort with the largest recoverable loss.
4. Explain its evidence and rank cases by expected net value.
5. Apply a deterministic policy gate before selecting an action.
6. Execute a Test Mode payment link, mock message, retry, promise, escalation, or stop.
7. Record the outcome and compare adaptive, rules-based, and Day 0/1/3 policies.

## Constraints

- Do not handle pricing, tax, refunds, reconciliation, fraud, chargebacks, real WhatsApp delivery, real customer data, or real money.
- Do not let an LLM grant permission for an action. Policy owns permission and tools own side effects.
- Treat synthetic evaluation as simulator evidence, not proof of merchant recovery lift.
- Keep all side effects idempotent, auditable, bounded to Test Mode, and disabled by a kill switch when required.

## Policy rules

- Do not act on paid, refunded, disputed, opted-out, or closed cases.
- Deduplicate provider events before opening a recovery case.
- Do not automatically retry hard declines.
- Limit each case to three contacts and one action in a 24-hour period.
- Respect configured Asia/Kolkata quiet hours.
- Use the exact outstanding amount in a payment link.
- Require an idempotency key for every side effect.
- Require human approval for discounts or amount changes.
- Cancel pending action after a successful payment.
- Escalate or stop when consent or required identity information is absent.

## Architecture

1. Ingestion verifies webhooks, stores raw bodies, deduplicates, and normalizes events.
2. Leak analysis calculates failure cohorts and their recoverable impact.
3. Recovery scoring estimates action-conditioned recovery probability.
4. The policy engine filters actions before the controller can select one.
5. The controller produces structured decisions and calls one allowed tool.
6. Tools create side effects, record idempotency keys, and write audit records.
7. Evaluation separates simulated and Test Mode outcomes from estimates.

## Data model

The system persists customers, payment events, recovery cases, leak findings, decisions, action events, outcomes, and immutable audit events.

Payment events retain provider event identity, payment and customer identity, amount, method, status, error details, time, provider, raw hash, and raw body. Recovery cases retain amount at risk, state, attempts, opening time, and stop reason.

## Delivery phases

### Phase 1. Deterministic foundation

- Build FastAPI configuration, database tables, and migrations.
- Normalize events, verify Razorpay raw-body signatures, and deduplicate events.
- Import CSV data, manage recovery-case state, and write audit records.

Status: completed in commit `6f6a26c`.

### Phase 2. Leakage intelligence

- Aggregate cohorts by method, error reason, customer history, amount, and time bucket.
- Calculate failure lift, impact, confidence, and recoverable impact.
- Store versioned findings with source event IDs.
- Confirm the seeded hidden scenario ranks the expected leak first.

### Phase 3. Simulator and baseline

- Generate seeded customers, events, and hidden response probabilities.
- Implement the fixed Day 0/1/3 baseline with contact, retry, and discount costs.
- Run reproducible evaluations with identical cases and random seeds.

### Phase 4. Recovery model

- Generate case-action outcome rows and split by customer.
- Train and calibrate logistic regression.
- Rank allowed actions by expected net value.
- Publish model assumptions and limits.

### Phase 5. Policy and tools

- Implement every policy rule.
- Add Razorpay Test Mode payment links, mock messaging, retry scheduling, promise-to-pay, and escalation tools.
- Make every tool idempotent and auditable.

### Phase 6. Controller

- Add structured action selection using evidence, policy output, and recovery scores.
- Reject malformed or blocked actions.
- Add message composition and deterministic fallback behavior.

### Phase 7. Dashboard

- Build executive, investigation, worklist, timeline, evaluation, and mock-inbox views.
- Keep estimated values separate from simulated and Test Mode values.

### Phase 8. Evaluation and hardening

- Run at least 30 identical-seed comparisons across policies.
- Test duplicates, late success, opt-out, hard declines, and provider failures.
- Freeze results, exceptions, and published claims.

### Phase 9. Submission

- Document architecture, prior art, threats, evaluation, setup, and limits.
- Provide demo data and a five-minute video.
- Audit the public repository for secrets and real customer data.

## Verification targets

- The detector ranks the expected top leak on the hidden scenario.
- Every decision has evidence, a policy version, and a model version.
- No hard-decline retries or stop-rule violations occur in the published evaluation.
- Adaptive, rules-based, and fixed policies evaluate the same cases under the same seeds.
- A new reviewer can run the project with documented local commands.
