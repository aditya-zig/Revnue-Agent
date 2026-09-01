# ReRoute Intelligence

ReRoute turns failed payment observations into policy gated recovery work. It keeps raw provider events separate from the case that tracks recovery.

## Language

**Customer**:
A payer identified by customer_id who has tenure, payment history, consent, and locale.
_Avoid_: User, client, buyer, account

**MerchantRole**:
The demo responsibility held by a business owner or operations worker. It records who configured Policy or acted on a Case but does not provide authentication.
_Avoid_: User permission, login, access control

**RecoveryContactConsent**:
The explicit Customer-level permission to receive recovery communication. It retains its source and recorded time. Withdrawal stops all open RecoveryCases for that Customer.
_Avoid_: Email permission, marketing consent, case consent

**PaymentEvent**:
A normalized record of one Razorpay or CSV payment observation with amount, method, status, and error fields.
_Avoid_: Transaction, payment failed, webhook

**PaymentObligation**:
A merchant-owned reference to the amount a Customer owes. It groups related payment attempts and owns the balance that a RecoveryCase may recover. When no durable reference exists, the payment attempt remains isolated until a human links it.
_Avoid_: Order, invoice, subscription, payment attempt

**RecoveryCase**:
A permanent workflow object for one PaymentObligation. It owns state, decisions, actions, and outcome and never reopens after resolution.
_Avoid_: Ticket, case file, recovery

**PaymentException**:
A separate workflow for a PaymentObligation when a Customer reports a debit or the provider signals a reversal. It records evidence and resolves as no debit, reversed, captured, or refunded. An open PaymentException blocks the related RecoveryCase from customer-directed Actions.
_Avoid_: RecoveryCase, refund case, failed payment

**LeakFinding**:
A cohort aggregation with at least three supporting PaymentEvents, ranked by recoverable impact.
_Avoid_: Leak, insight, root cause

**FindingAnalysis**:
An immutable advisory record created by an explicit operator request from a sanitized LeakFinding aggregate snapshot. It may contain bounded OpenRouter hypotheses and validation steps, or a deterministic local fallback. It preserves finding provenance without depending on the detector row and separates observed facts from hypotheses.
_Avoid_: Model explanation, diagnosis, forecast

**Policy**:
The deterministic gate that decides which Actions are allowed for a Case before any scoring.
_Avoid_: Guardrail, rule engine, filter

**RecoveryModel**:
A local logistic scorer trained on synthetic rows that ranks allowed Actions by expected net value.
_Avoid_: AI, LLM, adaptive agent, predictor

**Action**:
One of payment_link, contact, retry, promise, escalate executed with idempotency and audit.
_Avoid_: Message, notification, retry email, WhatsApp

**Outcome**:
The resolved result of a Case with recovered flag, amount, and source, kept separate from estimates and simulations.
_Avoid_: Recovery result, payment success, recovered money

**AuditEvent**:
An append only record of every state and action transition for a Case timeline.
_Avoid_: Log, history entry, event log

**ClaimTag**:
One of ESTIMATED, SIMULATED, TEST MODE, or MOCK identifies a money figure when its provenance is known and homogeneous. Mixed or unknown-source aggregates omit the tag. Booked revenue, forecast, and lift are forbidden.
_Avoid_: Provenance, value type, confidence label

**SyntheticCorpus**:
Generated PaymentEvents, Customers, and RecoveryCases that feed Investigation and Worklist, including the two named edge cases. Not EvaluationComparison and not merchant data.
_Avoid_: Demo data, seed file, merchant sample

**EvaluationComparison**:
A thirty seed by thirty case synthetic policy comparison on identical generated cases. Not SyntheticCorpus and not merchant recovery evidence.
_Avoid_: A/B test, lift study, backtest

**Revenue at Risk**:
The sum of amount_at_risk on RecoveryCases whose state is not recovered.
_Avoid_: GMV, outstanding, open cases count

**Estimated Recoverable**:
The recoverable_impact of the single top LeakFinding. Overlapping cohorts are not summed.
_Avoid_: Expected value, forecast, recoverable impact total

**Actual Recovered**:
Test Mode Outcome amount only. Absence of Outcome means zero, not a simulated fill.
_Avoid_: Booked revenue, recovered money, Test Mode recovered sum

**Simulated Recovery**:
The recovered amount from EvaluationComparison for the adaptive policy.
_Avoid_: Adaptive lift, net recovery value, simulated revenue
