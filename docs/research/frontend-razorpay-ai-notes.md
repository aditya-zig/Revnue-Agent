# Frontend, Razorpay, and AI notes

Where this note lives: `docs/research/frontend-razorpay-ai-notes.md`. Repo keeps notes as `docs/*.md` like `docs/prior-art.md`, `docs/demo.md`.

## How the frontend looks

The frontend is a single file in `app/api/dashboard.py:206-246` that serves one HTML page at `GET /` (`app/api/dashboard.py:23`) and one JSON endpoint at `GET /api/v1/dashboard` (`app/api/dashboard.py:28`). The HTML embeds all CSS and JS inline, there is no separate asset bundle. README describes opening `http://127.0.0.1:8000/` after seeding (`README.md:28`).

Navigation has six buttons (`app/api/dashboard.py:223`):

* executive
* investigation
* worklist
* timeline
* evaluation
* mock inbox

The labels in the HTML are title case (`Executive`, `Investigation`, etc.) but the underlying view ids match the six sections listed in the task.

### Executive

Rendered at `app/api/dashboard.py:231-236`. Four cards:

* estimated recoverable value. Value comes from `estimated_value` which sums `expected_value` from decisions for all cases (`app/api/dashboard.py:35`). Tagged `ESTIMATED` and styled with orange border (`app/api/dashboard.py:218`). This is an estimate computed from the leak detector and recovery probability, not booked revenue. `docs/demo.md:23` explicitly says call the impact an estimate, not booked revenue or forecast.
* adaptive simulated recovery. Value comes from `evaluation.results.policies.adaptive.recovered_amount` (`app/api/dashboard.py:234`). Tagged `SIMULATED`. This is a synthetic simulation number from `app/evaluation/comparison.py:22` and frozen output in `app/evaluation/published_results.json:7`. `docs/demo.md:62` says this is simulation, not merchant revenue.
* test mode recovered. Value comes from `test_mode_value` which sums `recovered_amount` on `Outcome` rows (`app/api/dashboard.py:36-37`). Tagged `TEST MODE` with green border. Represents real Test Mode outcomes only when the Razorpay adapter was configured. In the default demo this is zero or whatever synthetic outcomes exist.
* open cases. Count of cases where `state not in {recovered, closed}` (`app/api/dashboard.py:44-46`). No tag.

What is empty or mocked: if no leak findings exist the executive still renders but `top_leak` is null, and investigation shows no leak message. Estimated and simulated values are not revenue.

### Investigation

Rendered at `app/api/dashboard.py:237`. Shows the top ranked leak finding only (`app/api/dashboard.py:48` picks `findings[0]` after sorting by `finding_sort_key` at `app/api/dashboard.py:32`). Display includes `finding_id`, `recoverable_impact` tagged `ESTIMATED RECOVERABLE IMPACT`, `confidence` as percent, cohort filter JSON, and evidence JSON. Source for finding fields is `_finding` helper at `app/api/dashboard.py:167-174` which maps `app/db/tables.py:60-72`. The detector is described at `docs/architecture.md:23-26`: groups by error, method, amount, time, customer history, needs at least three events.

What is mocked or empty: shows `No leak finding has been detected.` when there are no findings (`app/api/dashboard.py:237`). Evidence includes `data_quality_warnings` from `app/leak_analysis/detector.py:238-241` when dimension value is `unknown`. The confidence shown is a Wilson lower bound (`app/leak_analysis/detector.py:229-235`), not a calibrated business confidence.

### Worklist

Rendered at `app/api/dashboard.py:238`. Table with columns `Case`, `Evidence`, `Selected action`, `Policy`, `Human review`. Data comes from `_case_summary` at `app/api/dashboard.py:63-102`. Each row shows `case_id`, `amount_at_risk`, `state`, evidence `event_type` and `error_reason` or `status`, selected action and expected value tagged `EST.`, allowed actions list or `Blocked` in red, and approve buttons per allowed action.

Human review text at `app/api/dashboard.py:223`: `Human review can submit only actions allowed by the policy. Actions use Test Mode or mock tools.` The approve button issues `POST /api/v1/cases/{case_id}/actions` with a random `idempotency_key` (`app/api/dashboard.py:244`). `can_execute` is true only when `case.state == eligible` and allowed actions exist (`app/api/dashboard.py:99-101`). Policy comes from `app/policy/evaluate.py:32-96` evaluated live per request at `app/api/dashboard.py:79-85`.

What is mocked or empty: no real cases until ingestion runs. Actions that are blocked cannot be approved, the UI shows `No action permitted`. `retry` is blocked for hard declines (`docs/demo.md:34-36`), quiet hours blocks `contact` and `promise`, missing identity or consent blocks contact actions.

### Timeline

Rendered at `app/api/dashboard.py:239`. Intro text at `app/api/dashboard.py:223`: `Raw event, decision, action, audit record, and outcome share one case timeline.` Implementation at `app/api/dashboard.py:105-164` merges per case: payment events as `raw event` (`app/api/dashboard.py:123`), decisions as `decision` with null timestamp, actions as `action`, audit events as `audit`, and outcome as `outcome`. Events are shown with tags `TEST MODE` for outcome, `ESTIMATED` for decision, `SIMULATED` otherwise, and JSON detail via `_payment`, `_action`, and raw audit payload.

What is mocked or empty: shows `No events.` if case has no history. Decision entries have no timestamp (`at: None`). Timeline is grouped per `case_id`, not a global chronological feed.

### Evaluation

Rendered at `app/api/dashboard.py:240`. Tagged `SIMULATED`. Shows `identical-case seeds` and `cases per seed` from `evaluation.results`, plus raw JSON dump of the full evaluation object. Source is `get_published_evaluation` at `app/api/dashboard.py:51` which reads `app/evaluation/published_results.json` and `app/evaluation/published_exceptions.json` via `app/api/evaluations.py:16-20`. Reproducible endpoint reruns deterministic simulation at `app/api/evaluations.py:25-27` and `app/evaluation/comparison.py:21-31`.

What is mocked or empty: this evaluation is not merchant data. `docs/evaluation.md:2` says it creates 30 identical case sets with seeds 0 through 29, each 30 cases. Every policy receives the same generated cases. Money values are paise. Table at `docs/evaluation.md:11-15` shows adaptive 51,961,100 paise recovered, fixed retry 34,874,100 with 120 violations. `docs/evaluation.md:34` says results do not measure revenue lift.

### Mock inbox

Rendered at `app/api/dashboard.py:241`. Query at `app/api/dashboard.py:52-59` selects `ActionEvent` where `tool in [contact, promise]` ordered by `executed_at desc`. Each entry shows `tool for case_id`, tag `MOCK`, `status at executed_at`, and `provider_reference` or `no provider reference`. Helper `_action` at `app/api/dashboard.py:192-199` maps the four fields.

What is mocked or empty: shows `No mock messages have been sent.` when there are no contact or promise actions. This inbox never contains payment_link actions, retry actions, or real provider responses. The provider reference for `contact` and `promise` is `mock_{action}_{idempotency_key}` at `app/recovery/actions.py:104`, not an email or WhatsApp id. See section 5 below.

## How to simulate a payment dot failed

### Razorpay official behavior

* payload structure: Razorpay sends an event envelope with top level `entity: event`, `event: payment.failed`, `contains: [payment]`, and `payload.payment.entity` holding the payment entity. The payment object includes `id`, `amount`, `currency`, `status: failed`, `error_code`, `error_description`, `error_source`, `error_step`, `error_reason`, `created_at`, `notes`, and method specific fields. Sample payload at https://razorpay.com/docs/webhooks/payments/#payment-failed shows `notes: []`, `error_code: BAD_REQUEST_ERROR`, `error_source: bank`, `error_step: payment_authorization`. Payment entity fields at https://razorpay.com/docs/api/payments/entity/ list the same fields.
* additional failure context: Razorpay notes that a `payment.failed` can be followed by `payment.captured` for the same transaction when the user retries within a UPI app. This is expected behavior described at https://razorpay.com/docs/webhooks/payments/#payment-failed under Watch Out.
* signature: when a webhook secret is set Razorpay creates HMAC SHA256 over the raw request body with the secret as key and sends it in `X-Razorpay-Signature`. Verification guidance at https://razorpay.com/docs/webhooks/validate-test/#validate-webhooks says do not parse or cast the body before hashing, the raw body is the message and the hex digest of hmac sha256 is compared with the header using constant time comparison. Idempotency is via `x-razorpay-event-id` header, documented at https://razorpay.com/docs/webhooks/validate-test/#idempotency. Order of delivery is not guaranteed at https://razorpay.com/docs/webhooks/validate-test/#order-of-webhooks.
* test mode behavior: payload structure is identical in Test and Live mode. Testing docs at https://razorpay.com/docs/webhooks/validate-test/#test-webhooks say test events trigger on a transaction done in Test mode and the stage payload can be relied on. Separate URLs for Test and Live are configured on the dashboard at https://razorpay.com/docs/webhooks/#setup-and-configuration.
* local testing: Razorpay docs previously suggested tunneling services like ngrok. Current validate and test page at https://razorpay.com/docs/webhooks/validate-test/#application-running-on-localhost now recommends `zrok` because many common tunneling domains are blacklisted. Blacklisted list at https://razorpay.com/docs/webhooks/validate-test/#blacklisted-domains includes `ngrok.io`, `requestbin.com`, `webhook.site` and others. The only local path Razorpay documents today is `zrok` via https://docs.zrok.io/docs/zrok/getting-started.

### How this repo simulates it

This repo provides three separate simulation paths, none of which hit live Razorpay by default.

* webhook curl with HMAC signature. The endpoint at `app/api/webhooks.py:17-22` is `POST /api/v1/webhooks/razorpay` with status 202. It reads the raw body via `app/core/requests.py` with a size limit, verifies with `app/core/security.py:5-9` which does `hmac.new(secret.encode, body, sha256).hexdigest` and `hmac.compare_digest`. Failure returns 401 at `app/api/webhooks.py:29-32`. On success it calls `NormalizedPaymentEvent.from_razorpay(payload, sha256(body).hexdigest)` at `app/api/webhooks.py:36` and stores with deduplication. Integration tests at `tests/integration/test_webhooks.py:19-20` show how to compute the signature locally: `hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()` and send as `X-Razorpay-Signature`. The request body must be raw bytes from `json.dumps(payload, separators=(",", ":"))` not a re-serialized Python dict, otherwise the digest mismatches.
* CSV seed import. `scripts/seed_demo.py:34-40` calls `import_csv` on `demo/payment_events.csv`, then `detect_and_store_leaks`. The CSV at `demo/payment_events.csv:2-4` contains three synthetic `payment.failed` rows with method `upi`, error reason `insufficient funds`, amounts 100000 paise, plus three `payment.captured` rows for baseline. Two extra rows `demo_hard_decline` and `demo_provider_failure` seed specific edge cases. The loader at `app/ingestion/csv_loader.py:13-63` validates each row as a `NormalizedPaymentEvent` and upserts customers with tenure, consent, etc. The CSV path also covers offline demo without any network.
* direct API import. `POST /api/v1/data/import` accepts a UTF-8 CSV body and reuses the same CSV loader. Mentioned in `README.md:50` and `app/api/data.py`.

Normalization details. `app/domain/models.py:29-51` implements `from_razorpay`:

```
event_id = f"evt_{payload.get(id, raw_hash)}"
payment = payload["payload"]["payment"]["entity"]
payment_id = payment["id"]
customer_id = payment.get("notes", {}).get("customer_id")
amount = payment["amount"], currency = payment["currency"], method = payment.get("method")
status = payment["status"], error fields mapped from error_source, error_step, error_code, error_description
occurred_at from payment["created_at"] timestamp via datetime.fromtimestamp
provider = "razorpay_test", raw_hash = sha256(body).hexdigest(), raw_body = body
```

Note on `notes`: the official `payment.failed` sample has `notes: []` (empty array) at https://razorpay.com/docs/webhooks/payments/#payment-failed, but this repo expects `notes` to be a dict when `customer_id` is present (`app/domain/models.py:39`). Production Razorpay docs for Payments entity at https://razorpay.com/docs/api/payments/entity/ list `notes` as a json object. If the real payload carries `notes: []` then `.get(customer_id)` would raise `AttributeError` on a list, which is not caught by the `except (KeyError, TypeError, ValueError)` at `app/api/webhooks.py:38` and would bubble as 500. The tests work around this by sending `notes: {customer_id: cust_001}` at `tests/integration/test_webhooks.py:38`. Customer association therefore requires the merchant to put `customer_id` into payment notes at creation time, the field is optional and often absent.

Idempotency and dedup. Two layers:

* webhook dedup uses `provider_event_id` uniqueness at `app/db/tables.py:15` and `record_event_and_update_case` handling `IntegrityError` at `app/ingestion/record_event.py:9-15`. Duplicate returns `200 {event_id, status: duplicate}` with an `event.duplicate` audit at `app/api/webhooks.py:50-61`. Test duplicate behavior at `tests/integration/test_webhooks.py:48-61` and distinct events for same payment with differing bodies at `tests/integration/test_webhooks.py:115-152`.
* action idempotency uses `idempotency_key` uniqueness at `app/db/tables.py:92` and `app/recovery/actions.py:32-45` which returns the existing result without re-executing and raises if the key is reused for a different case or action.

## What is on the dashboard

The dashboard response shape is defined at `app/api/dashboard.py:39-60`:

```
{
  executive: { top_leak, estimated_value, test_mode_value, open_cases },
  investigation: finding or null,
  worklist: [ { case_id, payment_id, customer_id, amount_at_risk, state, evidence, selected_action, expected_value, policy, human_review } ],
  timeline: [ { case_id, events: [ { kind, at, data } ] } ],
  evaluation: { results, exceptions },
  mock_inbox: [ { case_id, tool, status, provider_reference, executed_at } ]
}
```

Section by section:

* executive. `top_leak` is the highest `recoverable_impact` finding after sorting by `finding_sort_key` (`app/api/dashboard.py:41`). If no findings, null and investigation shows empty message. `estimated_value` sums decisions expected values. This is labeled `ESTIMATED` in UI (`app/api/dashboard.py:218-219`). `test_mode_value` sums outcome recovered amounts. Labeled `TEST MODE` (`app/api/dashboard.py:218`). `open_cases` counts cases not in `recovered` or `closed`. None of these are real money, see warnings at `docs/demo.md:23` and `docs/evaluation.md:34`.
* investigation. Same object as `executive.top_leak` but rendered with cohort filter and evidence including `event_ids`, `support`, `failure_count`, `attempted_value`, `failed_value`, `unresolved_value`, `recovery_probability` from `app/leak_analysis/detector.py:208-216`.
* worklist. Per case `evidence` is from the latest `PaymentEvent` for that payment (`app/api/dashboard.py:64-68`), `selected_action` prefers the last `Decision` then last `ActionEvent` (`app/api/dashboard.py:93-96`), `expected_value` from decision, `policy` is full `PolicyResponse` dump (`app/api/dashboard.py:97`), `human_review.allowed_actions` mirrors `policy.allowed_actions` and `can_execute` gates on eligible state. This is the only place mutation is allowed via approve buttons.
* timeline. Merges raw events, decisions, actions, audit records, and outcome for each case. Raw events carry the full stripped payment fields via `_payment` at `app/api/dashboard.py:177-189` including `raw_hash` and decoded `raw_body`. Decisions carry `selected_action`, `expected_value`, `policy_version`, and `evidence` from `reason_json`. Outcome carries `recovered`, `recovered_amount`, `source`.
* evaluation. From `app/evaluation/published_results.json:1` and `app/api/evaluations.py:17-20`. Contains `seeds: [0..29]`, `cases_per_seed: 30`, and three policies with `recovered_amount`, `recovery_rate`, `recovery_cost`, `contacts`, `safety_violations`, `calibration_brier_score`. All numbers are synthetic. Run again via `GET /api/v1/evaluations/reproducible` at `app/api/evaluations.py:25`.
* mock inbox. Only `contact` and `promise` tools, sorted newest first. Shows provider reference which for those tools is `mock_{action}_{idempotency_key}` at `app/recovery/actions.py:104`, not an external id. Empty state is `No mock messages have been sent.` at `app/api/dashboard.py:241`.

Simulated vs test mode vs estimated tags in the UI:

* `ESTIMATED` orange left border is used for recoverable impact, expected value, and decision kinds (`app/api/dashboard.py:218`, `app/api/dashboard.py:233`, `app/api/dashboard.py:239`).
* `SIMULATED` blue border is used for evaluation recovery and event kinds that are synthetic (`app/api/dashboard.py:218`, `app/api/dashboard.py:234`, `app/api/dashboard.py:240`).
* `TEST MODE` green border is used for real gateway outcomes (`app/api/dashboard.py:218`, `app/api/dashboard.py:235`). In the default demo with no Razorpay keys configured, test mode recovered stays at zero and the payment link provider throws `RuntimeError` at `app/main.py:17` and `scripts/seed_demo.py:8`.

## What the AI does

There is no LLM. The repository contains a hand written logistic regression trained on synthetic data.

### Model that actually runs

* class `RecoveryModel` at `app/recovery/scoring.py:32-38` generates training rows in code with fixed seed 7 at `app/recovery/scoring.py:71-96`. It creates 60 customers, each with random tenure, successful payments, prior failures, and amount, then for each of the five actions `payment_link, contact, retry, promise, escalate` computes a hidden probability via a logit at `app/recovery/scoring.py:116-138` and samples recovery. Features at `app/recovery/scoring.py:141-149` are amount, tenure, payments, prior failures, and action one hots. It splits by hash of customer id into train 60 percent, calibration 20 percent, holdout 20 percent at `app/recovery/scoring.py:99-113` with no customer overlap between train and holdout by design.
* Training is gradient descent for 500 steps on logistic loss at `app/recovery/scoring.py:152-165` with L2, then 300 steps of Platt scaling calibration at `app/recovery/scoring.py:168-181`. `MODEL_VERSION = v1` at `app/recovery/scoring.py:9`. `REPORT` at `app/recovery/scoring.py:184-234` includes training and holdout counts, customer counts, Brier score, mean predicted probability, observed recovery rate, top k precision, expected and realized net value.
* `rank` at `app/recovery/scoring.py:40-68` takes a `RecoveryCase` and optional `Customer`, builds one candidate row per allowed action, computes calibrated probability via `_calibrated_probability` at `app/recovery/scoring.py:237-241`, computes `expected_net_value = round(prob * amount) - cost` where costs are `payment_link 25, contact 100, retry 250, promise 100, escalate 0` at `app/recovery/scoring.py:10-15`, and returns candidates sorted by expected net value descending.

Limits are documented at `docs/model-limits.md:1-11`: no merchant data, no external model service, no causal estimate, no monitoring, drift detection, fairness analysis, or confidence intervals. Money values are synthetic and do not establish performance for a merchant population.

### Deterministic policy gate vs scoring

Policy runs before scoring. `evaluate_policy` at `app/policy/evaluate.py:32-96` determines `allowed_actions` and `blocked_reasons`. It blocks:

* all actions on kill switch or terminal case states (`app/policy/evaluate.py:40-51`, states listed at `app/policy/evaluate.py:13-22`).
* `contact` and `promise` when customer missing or `consent` false (`app/policy/evaluate.py:53-59`).
* `contact` and `promise` when contact count already >=3 for the case (`app/policy/evaluate.py:61-69`).
* `contact` and `promise` during quiet hours computed in Asia/Kolkata (`app/policy/evaluate.py:71-74`).
* `retry` when any `PaymentEvent` for that payment has error code in hard decline set (`CARD_BLOCKED`, `CARD_DECLINED`, `CARD_EXPIRED`, `CARD_NOT_SUPPORTED`, `HARD_DECLINE`) at `app/policy/evaluate.py:76-80`, constants at `app/policy/evaluate.py:23-29`.
* all actions when an action was already executed within the last 24 hours (`app/policy/evaluate.py:82-90`).

The recovery model only receives the allowed list at `app/recovery/controller.py:58-59`:

```
policy = evaluate_policy(...)
scores = recovery_model.rank(case, customer, policy.allowed_actions)
if not scores: raise PermissionError(["no_allowed_action"])
```

So the model cannot override policy.

### Controller and fallback

`app/recovery/controller.py:15-116` owns `run_decision`:

* deduplicates decisions by `sha256(idempotency_key)` at `app/recovery/controller.py:27-32`, and replays the existing action without re-scoring.
* when no model function is present, selects fallback as `scores[0].action` at `app/recovery/controller.py:125`.
* when an optional `decide_recovery_action` callable is injected (`app/main.py:28` stores it as `app.state.decide_recovery_action`), validates it against `StructuredDecision` which has `extra=forbid` at `app/domain/models.py:76` and an allowlist of five literal actions at `app/domain/models.py:78`. Invalid output returns fallback with rejection `invalid_model_output: {error}` at `app/recovery/controller.py:129-130`. If the model picks a blocked action, returns fallback with rejection `blocked_action` at `app/recovery/controller.py:131-132`. The model is therefore advisory only, audited in `Decision.reason_json` at `app/recovery/controller.py:87-90`.

The comparison simulation at `app/evaluation/comparison.py:90-96` counts a safety violation when fixed retry retries a hard decline, contacts an opted out customer, or does anything other than escalate after a provider failure. Adaptive and rules based policies encode different selection rules at `app/evaluation/comparison.py:79-88`, not observed behavior.

## Email or WhatsApp message

Short answer: neither email nor WhatsApp is sent. The repo has a mock inbox.

### What actions exist

Five literal actions allowed by `ActionRequest` at `app/domain/models.py:60-62` and defined in `app/recovery/scoring.py:17` and `app/policy/evaluate.py:11`:

* `payment_link` — attempts to create a Razorpay Payment Link via injected `create_payment_link(amount, idempotency_key)` at `app/recovery/actions.py:105-107`. On success the action status is `completed` and the provider reference is the real link id. On failure it records `action.failed` and raises `ProviderError`.
* `contact` — mock only. Creates an `ActionEvent` with status `completed`, provider reference `mock_contact_{idempotency_key}` at `app/recovery/actions.py:104`, and writes audit `action.completed`.
* `retry` — mock with status `pending` at `app/recovery/actions.py:103`, provider reference `mock_retry_{idempotency_key}`.
* `promise` — same as contact.
* `escalate` — transitions case to `escalated` at `app/recovery/actions.py:126-127`.

All actions share the same idempotency, policy check, `action.blocked` audit on denial, `action.started` optimistic write, and state transitions at `app/recovery/actions.py:21-146`. `retry` is the only action that ends as `pending` rather than `completed` or `failed`.

### Does it send real email or WhatsApp

No. `README.md:8` states the repository contains synthetic data only and does not send customer messages. `docs/demo.md:48-49` says no real link or customer message was created when the default provider failure is replayed. `app/main.py:17` defines `_payment_link_not_configured` that raises `RuntimeError("payment link provider is not configured")`, so without injection every payment_link attempt fails and is recorded as `action.failed` at `app/recovery/actions.py:108-122` and surfaced as HTTP 502 by `app/api/cases.py`. Even when a real provider is injected, it would only create a Payment Link object, not send an email.

### Razorpay Payment Links notify dot sms and notify dot email

Razorpay Payment Links creation at https://razorpay.com/docs/api/payments/payment-links/create-standard/ takes a `notify` object with optional `sms: boolean` and `email: boolean`, and a `reminder_enable: boolean` for automated reminders. Docs at https://razorpay.com/docs/payments/payment-links/#advantages describe automatic sharing via SMS or email. The entity at https://razorpay.com/docs/api/payments/payment-links/entity/ echoes the same `notify.email` and `notify.sms` fields. There is no `notify.whatsapp` parameter in the Payment Links API. Razorpay does document a separate `Razorpay Payments on WhatsApp` product and a `Create Payment Links on WhatsApp` guide at https://razorpay.com/docs/payments/payment-links/ which is distinct from the standard payment links `notify` mechanism. The payment link itself also has a `whatsapp_link: boolean` read only field noted at https://razorpay.com/docs/api/payments/payment-links/create-standard/ but no create time WhatsApp notify flag.

This repo never sets `notify` or `reminder_enable`. Its payment link seam takes only `amount` and `idempotency_key` at `app/recovery/actions.py:29` and `app/main.py:17`, no customer contact is passed through. So it does not use even Razorpay's built in SMS or email notification.

### What mock inbox actually stores

Source is `ActionEvent` rows filtered to `tool in [contact, promise]` at `app/api/dashboard.py:56`. Columns at `app/db/tables.py:87-99`:

* `action_id` primary key derived from `sha256(idempotency_key)` at `app/recovery/actions.py:70`
* `case_id` foreign key
* `idempotency_key` unique
* `tool` string
* `input_hash` hex of `{action, amount}`
* `status` (`completed` for contact and promise, `pending` for retry, `failed` for payment link failures)
* `provider_reference` either `mock_{tool}_{key}` or real link id when configured, or null
* `executed_at` timestamp

The helper `_action` at `app/api/dashboard.py:192-199` surfaces only `case_id`, `tool`, `status`, `provider_reference`, `executed_at`. No message body, subject, or channel is stored. Audit trail has matching `action.started` and `action.completed` events at `app/recovery/actions.py:78-84` and `app/recovery/actions.py:131-141`.

## Where the user's previous data is

### What Razorpay already provides

Razorpay's own APIs hold the authoritative records. Primary references:

* Payments entity at https://razorpay.com/docs/api/payments/entity/ returns `id` (pay_*), `amount`, `currency`, `status` (`created`, `authorized`, `captured`, `refunded`, `failed`), `method` (`card`, `netbanking`, `wallet`, `emi`, `upi`), `order_id`, `email`, `contact`, `fee`, `tax`, `error_code`, `error_description`, `error_source`, `error_step`, `error_reason`, `notes`, `acquirer_data`, `created_at`, and nested `card` or `upi` objects. List and fetch endpoints at https://razorpay.com/docs/api/payments/.
* Customers entity at https://razorpay.com/docs/api/customers/entity/ returns `id` (cust_*), `name`, `email`, `contact`, `gstin`, `notes`, `created_at`. Endpoints at https://razorpay.com/docs/api/customers/.
* Orders entity (referenced via `order_id` on payments) returns order amount, currency, and status. Not detailed in this note but listed at https://razorpay.com/docs/api/orders/ and surfaced as `order_id` on the payment payload at https://razorpay.com/docs/webhooks/payments/#payment-failed.

Razorpay webhooks and REST APIs can therefore answer amounts, statuses, error details, and basic identity (email, contact). They do not by themselves give you tenure_days, successful_payments, prior_failures, preferred_method, consent, locale, or recoverable impact, those are derived or merchant owned.

### What this repo stores

Tables defined at `app/db/tables.py:11-124`:

* `customers` (`app/db/tables.py:48-58`): `customer_id` (primary key, matched to payment notes customer_id or CSV customer_id), `tenure_days`, `successful_payments`, `prior_failures`, `preferred_method`, `consent` boolean, `locale`. These are synthetic in the demo and come from `demo/payment_events.csv:1` columns `tenure_days, successful_payments, prior_failures, preferred_method, consent, locale` ingested at `app/ingestion/csv_loader.py:43-58`. When ingesting a webhook, the repo only creates a customer if `customer_id` is found in notes, otherwise no customer row is created.
* `payment_events` (`app/db/tables.py:11-30`): `event_id`, `provider_event_id` unique, `event_type`, `payment_id` indexed, `customer_id`, `amount`, `currency`, `method`, `status`, `error_source`, `error_step`, `error_code`, `error_reason`, `occurred_at`, `provider` (`razorpay_test` or `csv_import`), `raw_hash`, `raw_body` bytes. The raw body is kept for audit and hash at `app/domain/models.py:51` and exposed decoded in timeline via `_payment` at `app/api/dashboard.py:186-189`. `recovery_cases` are derived per `payment_id` at `app/ingestion/record_event.py:16` and `app/domain/state_machine.py`.
* `recovery_cases` (`app/db/tables.py:33-46`): `case_id` (`case_{payment_id}`), `customer_id`, `payment_id` unique, `amount_at_risk`, `state` (`detected`, `investigated`, `eligible`, `action_selected`, `awaiting_outcome`, `recovered`, `escalated`, `stopped`), `attempts`, `opened_at`, `stop_reason`. This case object does not exist in Razorpay, Razorpay has payments and payment links but no recovery case concept.
* related tables: `leak_findings` (`app/db/tables.py:60-72`), `decisions` (`app/db/tables.py:74-85`), `action_events` (`app/db/tables.py:87-99`), `outcomes` (`app/db/tables.py:102-112`), `audit_events` (`app/db/tables.py:115-124`). These are all repo owned, not mirrored from Razorpay.

### Gap noted for customer association

Official sample `payment.failed` has `notes: []` (empty array) at https://razorpay.com/docs/webhooks/payments/#payment-failed. The payments entity at https://razorpay.com/docs/api/payments/entity/ lists `notes` as a json object, but the webhook sample diverges. This repo reads `payment.get("notes", {}).get("customer_id")` at `app/domain/models.py:39`. If Razorpay sends the documented empty array, that line would attempt `.get` on a list. The practical fix is to set notes as a dict when creating the payment via Razorpay checkout or orders API. Without that, `customer_id` stays null, and downstream policy blocks `contact` and `promise` for `missing_identity` at `app/policy/evaluate.py:55-56`, and `RecoveryModel.rank` falls back to zeroed customer features at `app/recovery/scoring.py:49-51`. The CSV seeding avoids this by always populating `customer_id`.

## References

Razorpay primary docs cited inline:

* https://razorpay.com/docs/webhooks/validate-test/
* https://razorpay.com/docs/webhooks/payments/
* https://razorpay.com/docs/webhooks/
* https://razorpay.com/docs/api/payments/entity/
* https://razorpay.com/docs/api/customers/entity/
* https://razorpay.com/docs/api/payments/payment-links/create-standard/
* https://razorpay.com/docs/payments/payment-links/
* https://razorpay.com/docs/api/payments/payment-links/entity/
* https://razorpay.com/docs/api/customers/
* https://razorpay.com/docs/security/whitelists/#webhook-ips (referenced for IP whitelisting at https://razorpay.com/docs/webhooks/#setup-and-configuration)

Repo primary sources cited inline throughout with `path:line`.
