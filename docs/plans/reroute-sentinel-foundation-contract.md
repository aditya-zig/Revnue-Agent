# ReRoute Sentinel Foundation Contract

This is the implementation contract established by Primary Session 1. Read it together with `reroute-sentinel-implementation-programme.md` and `reroute-sentinel-integration-multisession-plan.md` before changing the payment data plane or incident engine.

## What stays authoritative

The existing `RecoveryCase`, deterministic Policy gate, approval boundary, Razorpay Test Mode webhook verification, action execution, `Outcome`, and case audit trail remain authoritative. Sentinel adds an incident/observability layer above them; it does not replace their safety semantics.

## Evidence provenance

Every normalized payment event carries one explicit `source_kind`:

- `razorpay_test` — provider evidence accepted through the genuine Razorpay Test Mode path;
- `simulated_merchant` — deterministic merchant-day/history data;
- `simulated_provider` — deterministic second-provider or provider-health simulation;
- `simulated_bank_rail` — deterministic sandbox-only bank/issuer/rail telemetry;
- `mock` — an action or signal that did not occur through a real provider.

`authenticity_verified=true` means the provider event passed the applicable authenticity check before normalization. For Razorpay webhooks, the stored `raw_hash` is the evidence/body hash; secrets and webhook signatures are not part of the incident evidence contract.

## Payment identity contract

`PaymentEvent` preserves distinct identities instead of overloading one field:

- `payment_id` — provider payment/attempt ID;
- `provider_event_id` — signed provider event ID when supplied, otherwise the signed-body hash identity already used by the existing webhook path;
- `provider_order_id` — PSP order ID when supplied;
- `merchant_order_reference` — merchant-owned obligation/order reference when supplied;
- `obligation_reference` — compatibility correlation key used by the existing RecoveryCase flow;
- `event_id` — normalized ReRoute event ID;
- `raw_hash` + `authenticity_verified` — provider evidence provenance without exposing secrets.

New code should prefer the explicit provider/merchant fields and keep `obligation_reference` only for backward compatibility with the existing recovery workflow.

## Incident persistence contract

`payment_incidents` is the population-level object above individual RecoveryCases. It stores:

- incident ID and lifecycle state;
- opened, updated, and resolved timestamps;
- detector version;
- cohort filter;
- baseline and observed metrics;
- affected attempt count;
- estimated amount at risk;
- detection confidence/evidence;
- provenance summary;
- optional analysis and recommendation references.

Lifecycle:

`detected -> investigating -> actionable -> recovery_in_progress -> monitoring -> resolved`

`actionable -> monitoring` is also permitted when no recovery execution is required. All transitions must go through the incident transition helper so an `incident_audit_events` record is written.

## Correlation tables

- `incident_payment_events` links an incident to normalized evidence.
- `incident_recovery_cases` links an incident to recoverable payment cases.
- `incident_audit_events` records incident state changes and later incident-level control-plane events.

Links are idempotent. The incident detail contract can reconstruct linked case IDs plus their Decision, ActionEvent, and Outcome IDs without duplicating those existing records.

## Sanitized evidence bundle

`IncidentEvidenceBundle` has two deliberately separate areas:

1. `observed_facts` and normalized evidence references — deterministic/system-owned facts;
2. `model_hypotheses` — reserved for later bounded AI analysis.

The evidence reference does not expose raw webhook bodies or customer PII. Session 3 must keep AI-authored hypotheses out of `observed_facts`.

## Stable foundation APIs

- `GET /api/v1/incidents` — list incident summaries.
- `GET /api/v1/incidents/{incident_id}` — deterministic incident facts, sanitized evidence, case/action/outcome chain, and incident audit.
- `POST /api/v1/incidents/{incident_id}/links` — idempotently link an event and/or RecoveryCase.
- `GET /api/v1/correlation/payment` — resolve provider payment ID, provider order ID, or merchant order reference to normalized events, cases, and incidents.

Later sessions may add endpoints, but should not casually rename these fields or redefine their semantics.

## Session 2 integration checklist

Before Session 2 starts, verify on current `main`:

- Alembic has one head and includes `0014_add_sentinel_incident_foundation`.
- Existing webhook, RecoveryCase, Policy, action, Outcome, and browser regression tests still pass.
- Razorpay signed webhooks persist `source_kind=razorpay_test` and `authenticity_verified=true` only after signature verification.
- Merchant replay rows use `simulated_merchant` unless another simulated adapter explicitly owns them.
- Detector-created incidents use the lifecycle helper and link their source events/cases idempotently.
- Simulated second-PSP and bank/rail signals use their dedicated provenance values and are never presented as Razorpay/bank facts.
- Detector logic remains deterministic; LLM output must not decide whether an incident exists.
