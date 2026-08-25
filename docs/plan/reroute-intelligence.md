# ReRoute Intelligence delivery plan

## Purpose

ReRoute turns failed payment observations into Policy-gated recovery work. The demo remains limited to synthetic data, mock delivery, and Razorpay Test Mode. It must not claim real merchant recovery or causal lift.

This replaces the imported six-day hackathon plan as the active delivery plan. The older plan is retained at `docs/plans/reroute-intelligence.md` as product history.

## Review of the imported plan

Most of its foundation has already landed:

- FastAPI ingestion, normalized `PaymentEvent` records, deduplication, `RecoveryCase` state, and `AuditEvent` records exist.
- `LeakFinding` detection, the seeded `SyntheticCorpus`, the fixed Day 0/1/3 baseline, Policy-gated action scoring, controller validation, idempotent actions, dashboard projections, and reproducible evaluation exist.
- Evaluation uses 30 identical generated case sets. It reports synthetic simulation only.

The imported plan cannot be followed literally:

- It schedules Streamlit, but the project deliberately uses a FastAPI dashboard in one process.
- It calls the scorer an LLM and an agent. The current design uses a deterministic `Policy`, a local `RecoveryModel`, and a controller.
- It mixes estimates, simulator outputs, and recovery claims. The project requires a `ClaimTag` on each money figure.
- It plans a new 500-event scenario and five-seed evaluation, which would replace existing reproducible data and published evaluation without a reason.

The next gap is product behavior. The open wayfinding map in GitHub issue #18 has not settled the merchant workflow, ownership, approval boundaries, `RecoveryCase` lifecycle, `PaymentException` handling, Customer communication, measures, or screen contracts. Building more UI before those decisions would hard-code guesses.

## Delivery goal

Ship a demo in which a merchant operator can inspect a `LeakFinding`, work an eligible `RecoveryCase`, understand why Policy allowed or blocked an `Action`, and distinguish ESTIMATED, SIMULATED, MOCK, and TEST MODE amounts.

## Scope

In scope:

- A settled operator workflow for confirmed failed `PaymentEvent` records and separate `PaymentException` workflows.
- Explicit Policy and approval boundaries for automated and human actions.
- A usable dashboard journey built on the existing FastAPI and SQLite application.
- Deterministic synthetic and Test Mode demonstrations with audit records.
- Reproducible evaluation and documentation that state what the numbers mean.

Out of scope:

- Production money movement, real Customer data, real email, SMS, or WhatsApp delivery.
- Authentication, deployment, production credentials, refunds, or an LLM.
- Replacing the existing FastAPI dashboard with Streamlit.
- Claims that simulator results are merchant revenue or observed lift.

## Order of work

### 1. Settle the product decisions

Work the open decision tickets on the #18 wayfinding map before implementation. Start with the payment obligation and attempt identity model, then decide the `RecoveryCase` lifecycle and `PaymentException` lifecycle. Those decisions determine safe Customer messaging, roles, dashboard measures, and screen behavior.

Record agreed terms in `CONTEXT.md` and hard-to-reverse choices in ADRs. Do not add new terms that compete with the glossary.

### 2. Write the build specification

Collapse the resolved map into one build specification. It must define user journeys, state transitions, data ownership, Policy decisions, approval points, message rules, dashboard screen contracts, and acceptance tests.

### 3. Create implementation tickets

Split the specification into tracer-bullet GitHub issues. Each ticket must make one complete, testable operator behavior work through persistence, API, dashboard projection, and tests. Link only genuine blockers and label ready issues `ready-for-agent`.

### 4. Implement the frontier

Implement one unblocked ticket at a time with a regression or behavior test first. Keep the existing FastAPI integration-test seam. Run the focused tests, the affected integration tests, lint, and type checks before committing. Review each ticket diff against its issue before the commit.

### 5. Verify the recorded demo

Run a clean local setup and execute the scripted demo. The recording must show the operator workflow, a Policy-blocked action, a permitted action with an `AuditEvent`, and the difference between ESTIMATED, SIMULATED, MOCK, and TEST MODE values.

## Acceptance conditions

- The #18 decision map has no open decision that changes the primary operator journey.
- The build specification names every state transition, Policy decision, and human approval point needed by the first release.
- Each implementation issue has behavior-level acceptance criteria and correct blocker links.
- The dashboard exposes only persisted data and uses the glossary terms.
- Every side effect remains idempotent and auditable.
- Evaluation replays identical generated cases per policy and labels its results SIMULATED.
- The documented local demo runs without provider production credentials.

## First frontier

Work the next unblocked decision on the GitHub wayfinding map. The identity model and `RecoveryCase` lifecycle are settled. `PaymentException` handling, Customer communication, roles, measures, screen contracts, and the low-fidelity prototype remain before implementation tickets.

These are decision tickets, not implementation tickets. After the map clears, create the implementation tickets from the specification and begin with the first unblocked one.
