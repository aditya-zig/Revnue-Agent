# ReRoute Sentinel Integration and Multi-Session Implementation Plan

## Purpose

This document explains how the project should be built across multiple AI coding sessions without losing context, duplicating work, creating merge conflicts, or calling unfinished code “done.” It is intentionally shorter than the Notion execution prompts. The Notion pages hold the detailed session instructions; this repository document is the durable coordination contract that every session should read.

## Source of truth

Repository: `aditya-zig/Revnue-Agent`

Deployment: `https://revnue-agent.vercel.app/`

Primary product brief: `docs/plans/reroute-sentinel-implementation-programme.md`

Notion master plan: `https://app.notion.com/p/New-plan-3d0074fda55680d0aaeff7e4a2a8d875`

Video planning: `https://app.notion.com/p/Video-planning-3d0074fda55680868fcee5c7d9a8801e`

Website planning: `https://app.notion.com/p/Website-planning-3d0074fda55680afa2c8ed09bba3598c`

If a session's prompt conflicts with code already merged to `main`, the session must inspect the current repository and adapt rather than blindly restoring stale assumptions.

## Final architecture

The hackathon system should connect the parties as follows:

```text
Customer / payer
    |
    v
Merchant storefront
    |
    +--> Razorpay Test Mode order / Checkout
    |        |
    |        +--> payment/provider outcome
    |        |
    |        +--> signed webhook ---------------------+
    |                                                  |
    +--> optional simulated second PSP                 |
    |                                                  v
    +--> optional simulated bank/rail telemetry --> ReRoute normalized event layer
                                                       |
                                                       v
                                            deterministic incident detector
                                                       |
                                                       +--> cohort / impact facts
                                                       |
                                                       v
                                            sanitized evidence bundle
                                                       |
                            +--------------------------+---------------------+
                            |                                                |
                            v                                                v
                    bounded AI analysis                            deterministic policy
                            |                                                |
                            +--------------------------+---------------------+
                                                       |
                                                       v
                                           permitted recommendation
                                                       |
                                                       v
                                            merchant notification
                                                       |
                                                human approval
                                                       |
                                                       v
                                             recovery execution
                                                       |
                                                       v
                                           Razorpay Test Mode outcome
                                                       |
                                                       v
                                         audit + recovered revenue proof
```

## Identity and correlation

Every provider integration should normalize enough identifiers to reconstruct the chain without exposing sensitive payment credentials.

At minimum preserve/correlate:

- `merchant_order_reference` or obligation reference;
- provider name;
- provider order ID;
- provider payment ID;
- provider event/webhook ID where supplied;
- pseudonymous customer ID;
- normalized PaymentEvent ID;
- Incident ID;
- RecoveryCase ID;
- Action/Decision ID;
- Outcome ID;
- signed-webhook provenance/hash.

The system should be able to answer: **which incident contained this payment, which recommendation produced this action, who approved it, what provider evidence later established the outcome?**

## Provider abstraction

Use a normalized provider contract instead of spreading Razorpay-specific fields across the product.

Conceptually, an adapter should expose functions such as:

- normalize provider event;
- verify event authenticity where possible;
- create checkout/order if supported;
- create recovery/payment link if supported;
- retrieve provider payment/order status if supported;
- map provider error metadata into sanitized normalized fields;
- report adapter capability flags.

Razorpay is the real Test Mode adapter. A second provider may be simulated if another sandbox integration is not worth the implementation cost.

Do not pretend simulated telemetry is real. Provenance must travel with every normalized event.

## Bank / issuer / UPI signal handling

Ordinary merchants do not automatically receive direct privileged telemetry from issuing banks, acquiring banks or NPCI. The prototype must not invent such access.

Use three evidence levels:

1. **Provider-exposed metadata** — real fields supplied by Razorpay or another connected PSP.
2. **Derived observations** — deterministic aggregation from merchant-owned payment events.
3. **Simulated external telemetry** — only for the sandbox; labelled `SIMULATED` in storage, APIs, UI and audit evidence.

If a root cause cannot be distinguished from available evidence, the analysis should say “likely” / “consistent with” and show uncertainty instead of asserting a bank outage as fact.

## Five-session build model

The implementation should be executed as five substantial sessions rather than many tiny ones.

### Session 1 — Control Tower + Architecture Foundation

Mission:

- audit the current repository, open PRs, deployment and tests;
- establish a clean baseline;
- read this document and the product programme;
- map reusable versus legacy code;
- implement or finalize Incident/provider/correlation data contracts;
- add migrations and core APIs necessary for later sessions;
- create/maintain an integration checklist;
- ensure old ReRoute workflows continue to function while the new layer is introduced.

This session owns foundational schemas/contracts. Later sessions should not redefine them casually.

### Session 2 — Payment Data Plane + Incident Engine

Mission:

- harden the Razorpay Test Mode adapter and signed webhook correlation;
- implement the normalized provider event path;
- add deterministic simulated second-PSP and bank/rail telemetry only where useful;
- convert the old 999-event simulator into a merchant-day replay engine;
- inject known incident windows;
- implement deterministic anomaly/cohort detection;
- quantify estimated revenue at risk;
- implement reproducible detector evaluation.

The LLM must not be the anomaly detector.

### Session 3 — Investigation + Policy + Recovery Control Plane

Mission:

- build the sanitized IncidentEvidenceBundle;
- implement bounded AI incident analysis and deterministic fallback;
- separate observed facts from hypotheses;
- apply deterministic policy before model ranking;
- preserve hard-decline/contact/quiet-hour/kill-switch safety rules;
- produce recommended permitted actions;
- require merchant approval for consequential actions;
- execute real Razorpay Test Mode recovery only where supported;
- persist action/outcome/audit linkage to the incident.

### Session 4 — Merchant Product + Interactive Sandbox

Mission:

- transform the frontend from admin panel into operator console;
- simplify navigation and money formatting;
- make Home feel live without fake decorative noise;
- replace visible `Simulate 999 Payments` with `Start interactive demo` / `Replay merchant day`;
- remove manual Refresh from the intended journey through polling/automatic refresh where appropriate;
- implement self-guiding incident notification and detail flow;
- implement investigation progress, facts/AI/policy/recommendation/approval/outcome states;
- improve storefront and judge entry point;
- visually verify every relevant state in a browser at desktop recording size.

A green build is not sufficient. This session must inspect the rendered website.

### Session 5 — Integration, Deployment, Red Team + Submission Readiness

Mission:

- pull together all merged work;
- resolve integration gaps;
- run clean-database migrations and full test suite;
- run end-to-end browser tests through the exact judge path;
- verify Vercel Preview/production and Supabase/configuration if used;
- verify no secrets or misleading evidence labels;
- red-team the product from a Razorpay judge's perspective;
- fix broken buttons, confusing copy and false claims;
- update README/demo/submission docs;
- establish a deterministic recording state for the five-minute video.

This session should be willing to remove features that weaken the demo.

## Git coordination rules

Each session must begin with repository reality, not prompt memory.

1. Inspect `main`, recent commits, open PRs and active work.
2. Fetch/update `main` before branching.
3. Run baseline tests before making large changes.
4. Use a scoped branch when work is concurrent or risky.
5. Do not modify files owned by another active session unless coordination requires it.
6. Commit in reviewable units.
7. Self-review the final diff.
8. Rebase/update against latest `main` before merging.
9. Verify the deployment when deployed behavior changed.

Suggested branch prefixes:

- `sentinel/s1-foundation-*`
- `sentinel/s2-data-incident-*`
- `sentinel/s3-control-recovery-*`
- `sentinel/s4-product-ui-*`
- `sentinel/s5-integration-*`

## “Push directly when confident” rule

Do not claim literal 100% certainty. Treat “confident enough to merge without waiting for a manual reviewer” as an objective gate.

A session may merge/push completed work only after all relevant gates pass:

- full relevant tests pass;
- regression suite passes;
- lint/type/build checks pass;
- `git diff --check` clean;
- secrets/privacy review clean;
- migrations clean if applicable;
- no known runtime/browser errors;
- Vercel Preview/production healthy when relevant;
- UI inspected visually when relevant;
- main has not moved unexpectedly;
- complete diff self-reviewed.

If a gate fails or cannot be verified, leave a branch/PR and document the blocker. Do not convert uncertainty into an assertion of confidence.

## Vercel and browser verification

Sessions that affect deployed functionality must inspect deployment results.

For frontend/UI work:

- open the actual preview/production page;
- test the intended click sequence;
- inspect console/runtime errors where tooling allows;
- inspect the layout visually;
- use approximately 1920×1080 desktop framing for the primary demo check;
- fix broken spacing, clipping, unreadable hierarchy or non-working controls before stopping.

Do not accept “tests passed” if the product looks broken.

## Reuse before rewrite

The current repo already has valuable correctness work. Preserve and adapt:

- Razorpay Test Mode integration;
- signed webhook validation;
- idempotency;
- RecoveryCase state machine;
- Policy;
- ranking;
- human approval;
- Outcome/audit evidence;
- evaluation harness;
- OpenRouter fallback path;
- browser/integration tooling.

The pivot should primarily add the Incident/observability layer and change the product experience. It should not destroy proven payment safety simply to create a new UI.

## End-to-end acceptance path

A final build should support the following deterministic judge journey:

1. Open public judge/demo URL.
2. Start clearly labelled simulated merchant replay.
3. See normal payment activity establish a baseline.
4. Injected UPI/provider incident begins without user pressing a failure button.
5. Detector opens an incident automatically.
6. Home shows an actionable merchant alert with affected cohort and estimated risk.
7. Open incident detail.
8. See deterministic facts and sanitized provenance.
9. See AI hypotheses/uncertainty separately.
10. See Policy allowed/blocked actions, including a hard-decline safety example.
11. See AI-ranked recommendation among only allowed actions.
12. Approve an eligible recovery.
13. Execute a genuine Razorpay Test Mode path where possible.
14. Receive/persist authoritative provider outcome evidence.
15. UI updates without a manual refresh.
16. Show recovered Test Mode amount and audit chain.
17. Open Evaluation and show reproducible simulated detector/recovery metrics.

## Session handoff format

Every session must finish with a concise handoff containing:

- branch / PR / commit SHA;
- whether work is merged into `main`;
- files/modules changed;
- database migrations added;
- tests executed and exact result;
- Vercel deployment URL/status if relevant;
- browser states visually verified;
- real vs simulated integrations used;
- security/privacy notes;
- remaining risks/blockers;
- exact prerequisites for the next session.

The next session must verify these claims in GitHub rather than trusting the handoff blindly.

## Final product standard

The project is complete when ReRoute Sentinel behaves like an operational payment product, not when all planned code exists.

A merchant or judge should be able to understand the product from the deployed UI without being taught the codebase first. The app must make the revenue problem, evidence, AI role, policy boundary, human decision and outcome visible in one coherent story.