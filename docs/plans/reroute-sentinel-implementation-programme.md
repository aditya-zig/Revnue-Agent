# ReRoute Sentinel Implementation Programme

## Purpose

This document is the durable project brief for ReRoute Sentinel. A new engineer or AI coding session should be able to read this file and understand what the product is, why it exists, what must remain truthful, how the architecture works, what parts of the existing ReRoute codebase should be reused, and what a complete demo must prove.

## Product decision

**Buildathon track:** Track 03 — AI Revenue Recovery.

**Product:** **ReRoute Sentinel — AI Payment Incident Autopilot.**

**One-line positioning:** ReRoute Sentinel watches a merchant's payment traffic, detects abnormal revenue-impacting incidents, investigates the likely cause, recommends only policy-safe recovery actions, keeps consequential execution under human control, and proves whether the intervention actually recovered money.

A useful mental model is **payment observability + bounded recovery**, not “another retry engine.”

The merchant should not spend the day staring at payment dashboards. Their business runs normally. ReRoute works in the background and interrupts them only when there is a material, actionable payment problem.

## Why the project changed

The original ReRoute Intelligence prototype had a strong safety and evidence core, but the product story was too close to a hackathon simulator:

- click **Simulate 999 Payments**;
- manually inspect failure data;
- manually find a case;
- investigate it;
- approve a recovery;
- manually refresh to see changes.

That flow proved backend capability but did not feel like software a merchant would actually use.

The revised product starts from the merchant's real job:

> “My business is processing payments. Tell me only when something important changes, explain what is happening, tell me what I can safely do, and prove whether the fix worked.”

## Market wedge

Do **not** pitch ReRoute as if payment companies lack retries, routing, analytics, payment links, subscriptions, fraud systems, or AI. Razorpay and other major payment platforms already have substantial capabilities in those areas.

The sharper wedge is the merchant-side **incident loop**:

1. detect a meaningful degradation across a population of transactions;
2. isolate the affected cohort;
3. quantify business impact;
4. combine heterogeneous evidence into a diagnosis;
5. decide which interventions are permitted;
6. rank permitted recovery actions;
7. request merchant approval when required;
8. execute a bounded action;
9. observe provider outcome evidence;
10. measure what changed and what revenue was actually recovered.

A router answers, “where should this transaction go?” Sentinel answers, “what changed across the payment system, who is affected, what is the likely cause, what response is safe, and did the response work?”

The project should remain **PSP-agnostic by architecture** even if the hackathon uses Razorpay Test Mode as its only real provider integration.

## Primary user

The main user is a merchant operations / founder / finance / payments owner who:

- processes enough payments that individual failures are noisy;
- does not want to investigate raw gateway errors manually;
- may use one or more payment providers;
- cares about lost revenue and payment reliability;
- needs a safe approval boundary before customer-facing or money-related actions.

The product is not designed primarily for the customer/payer and is not a consumer payment app.

## Core user journey

The ideal product journey is:

**Merchant operates normally → ReRoute detects incident → ReRoute investigates automatically → merchant is pinged with an actionable summary → merchant reviews evidence/policy/recommendation → merchant approves → ReRoute executes permitted recovery → provider outcome arrives → ReRoute records recovery and audit evidence.**

Example incident:

- baseline UPI success rate: 91.8%;
- current cohort success rate: 58.3%;
- 37 affected attempts;
- ₹46.2K estimated revenue at risk;
- failures concentrated on one provider/bank/UPI cohort;
- likely technical degradation rather than customer insufficient funds;
- recoverable cases separated from non-recoverable hard declines;
- recommended action: alternate payment path / payment link for eligible failures;
- merchant approval required;
- later Razorpay Test Mode `payment.captured` evidence records an actual ₹2,499 recovery.

The exact demo numbers may change, but the relationship between facts, estimates, simulated data and Test Mode outcomes must remain explicit.

## What is AI and what is not

AI must be useful, but it must not be given authority over money.

### Deterministic/statistical components

Use conventional code for:

- event normalization;
- webhook verification;
- idempotency;
- cohort aggregation;
- historical baselines;
- anomaly thresholds/statistical detection;
- revenue-at-risk calculation;
- policy permission;
- contact limits;
- hard-decline rules;
- quiet hours;
- approval requirements;
- state transitions;
- execution idempotency;
- provider outcome verification;
- evaluation metrics.

### AI/LLM components

Use AI for bounded work that benefits from reasoning across heterogeneous evidence:

- summarize an incident in merchant language;
- identify plausible hypotheses from normalized facts;
- connect provider errors, timing, payment method and cohort behaviour;
- propose validation steps;
- rank or explain **only actions policy already permitted**;
- generate a concise recommendation and uncertainty statement.

If the model is unavailable or malformed, the product must fall back to deterministic analysis rather than breaking the recovery workflow.

### Authority chain

**Facts → deterministic detector → deterministic policy → bounded AI analysis/ranking → human approval → provider action → provider evidence → persisted outcome.**

## Privacy and trust boundary

ReRoute must not ingest or send to an LLM:

- PAN/card number;
- CVV;
- OTP;
- raw bank credentials;
- Razorpay API secrets;
- webhook secrets;
- unnecessary customer PII.

Prefer normalized, minimized fields:

- merchant order/reference ID;
- provider order/payment/event IDs;
- pseudonymous customer ID;
- amount/currency;
- timestamp;
- payment method category;
- sanitized provider error code/reason;
- issuer/bank/route metadata when genuinely exposed;
- aggregate cohort statistics;
- prior outcome/recovery features;
- provider evidence provenance/hash.

## Parties and integrations

### Real integration path

Customer → merchant storefront → Razorpay Test Mode Checkout/order/payment → signed Razorpay webhook → normalized ReRoute PaymentEvent → detector → incident → analysis/policy/recommendation → merchant approval → recovery action → Razorpay Test Mode → signed outcome webhook → Outcome/Audit.

### Bank / issuer / NPCI boundary

Do not invent direct bank or NPCI access.

ReRoute may use bank/issuer/UPI metadata only when a connected PSP genuinely exposes it. If the interactive sandbox needs richer provider/bank health signals to demonstrate cross-system diagnosis, those signals must come from a clearly labelled **SIMULATED BANK / RAIL TELEMETRY** adapter.

### Second PSP boundary

The architecture should support multiple providers through a normalized provider adapter interface. A second real sandbox integration is optional. Do not delay the main product to integrate another PSP merely for visual complexity. A deterministic **SIMULATED SECOND PSP** is acceptable if the UI and audit trail label it honestly.

## Evidence labels

These labels are product invariants:

- **SIMULATED** — deterministic merchant-day history, injected incidents, simulated bank/rail or second-provider signals.
- **ESTIMATED** — recoverable impact, revenue at risk, model probabilities.
- **TEST MODE** — genuine Razorpay Test Mode provider objects/evidence.
- **MOCK** — actions such as simulated messaging that did not actually occur through a real provider.

Never display a local fixture as “Razorpay delivered this webhook.”

## Existing repository assets to keep

Repository: `aditya-zig/Revnue-Agent`

Deployment: `https://revnue-agent.vercel.app/`

The current project already contains useful pieces that should be refactored rather than discarded:

- Python 3.12 / FastAPI;
- SQLAlchemy + Alembic persistence;
- normalized PaymentEvent work;
- deterministic 999-payment corpus;
- LeakFinding / RecoveryCase concepts;
- state machine;
- deterministic Policy gate;
- RecoveryModel ranking;
- human approval checks;
- Razorpay Test Mode order/Checkout flow;
- signed webhook verification and idempotency;
- Payment Link/recovery action support;
- Outcome and audit trail;
- reproducible synthetic evaluation;
- bounded OpenRouter FindingAnalysis with deterministic fallback;
- browser/integration tests and demo tooling.

## Main domain changes

The revised product needs an **Incident** abstraction above individual cases.

Suggested conceptual objects:

### ProviderEvent / PaymentEvent
Normalized payment/provider evidence with provenance.

### PaymentIncident
Represents a population-level degradation. It should contain:

- incident ID;
- state;
- opened/updated/resolved timestamps;
- cohort dimensions;
- baseline metrics;
- current metrics;
- affected count;
- estimated amount at risk;
- confidence/detection evidence;
- provider/source provenance;
- linked recovery cases;
- diagnosis/recommendation reference.

### IncidentEvidenceBundle
Sanitized facts passed to analysis, with clear source labels.

### IncidentAnalysis
Observed facts, hypotheses, uncertainty, suggested validation, model/fallback metadata.

### PolicyDecision
Permitted and blocked actions plus reasons/version.

### RecoveryCase
An individual recoverable payment/customer obligation linked to an incident.

### Action / Approval / Outcome
Existing recovery workflow concepts remain, but they should now be visibly connected to the incident that triggered them.

## Detection and evaluation

The detector should be deterministic and reproducible.

The interactive sandbox should replay a synthetic merchant day and inject known incident windows. Detection should compare a cohort against an appropriate historical or peer baseline and open an incident only when defined gates are satisfied.

Evaluation must report honest quantitative performance such as:

- incident detection precision/recall on planted incidents;
- false-positive count;
- detection latency in replayed event-time;
- cohort attribution accuracy;
- estimated-at-risk calibration where feasible;
- eligible vs blocked recovery classification;
- policy violations: must be zero;
- recovery rate/recovered amount for simulated evaluation, labelled SIMULATED;
- outcome correctness for genuine Razorpay Test Mode recovery.

The system should not rely on an LLM to decide whether a statistical incident exists.

## Product experience

The app should feel like an operator console, not an admin panel.

### Home

Home should immediately answer:

- Are payments healthy?
- Is something changing now?
- What needs my attention?
- How much revenue is exposed?
- What should I do next?

Show quiet live payment activity, current success health, recent incidents and one dominant next action.

Visible developer controls such as **Simulate 999 Payments** and **Refresh data** should not be the primary user journey. Replace them with a clearly labelled interactive experience such as **Start interactive demo** or **Replay merchant day**. The simulator can remain under the hood.

### Incident detail

A strong incident detail page should distinguish:

**Facts** — deterministic evidence.

**AI assessment** — hypotheses and uncertainty.

**Policy** — what is allowed/blocked and why.

**Recommendation** — ranked permitted action.

**Human gate** — approval required.

**Outcome** — provider-backed result and recovered amount.

### Money formatting

Do not render both `₹` and `INR` together. Use readable Indian abbreviations for large amounts, for example `₹1.17L` or `₹2.34Cr`, and show the exact amount beneath where useful.

### Visual direction

Use a restrained finance-product visual language:

- light/off-white backgrounds;
- white surfaces;
- charcoal typography;
- one deep blue/indigo accent;
- green only for safe/recovered;
- amber for attention;
- red for failure/blocked;
- thin borders;
- little shadow;
- no neon gradients;
- no generic glassmorphism;
- no decorative AI sparkle effects.

Desktop video framing is the priority, but smaller widths must not break.

## Five-minute demo target

The final demo should not feel like a feature tour.

1. A short produced intro establishes the problem.
2. Merchant business is visibly running.
3. An incident develops in the background.
4. Sentinel detects it automatically.
5. Merchant receives an actionable notification.
6. Incident page shows baseline vs current performance and revenue at risk.
7. AI explains likely cause using sanitized facts.
8. Policy visibly blocks at least one unsafe action.
9. AI recommends among allowed actions.
10. Merchant approves one recovery action.
11. Razorpay Test Mode path produces a real test outcome.
12. UI updates without a manual refresh.
13. ReRoute shows recovered money and provider evidence.
14. Evaluation proves the detector/recovery logic on reproducible simulated incidents.

Core phrase for the product/video:

**Find the leak. Recover safely. Prove the outcome.**

## Definition of done

A judge who opens the public URL with no explanation should be able to understand:

- what Sentinel watches;
- what problem it detected;
- who/what is affected;
- how much money is at risk;
- which information is factual vs inferred;
- what policy allowed and blocked;
- what the system recommends;
- where human approval occurs;
- what was real Test Mode vs simulated;
- whether revenue actually came back;
- what evidence proves the result.

Do not optimize the product for the codebase. Optimize the codebase for this user journey.