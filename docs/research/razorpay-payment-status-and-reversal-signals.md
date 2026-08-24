# Razorpay payment status and reversal signals

Research date: 2026-08-25. This note covers Razorpay Payment Gateway Test Mode. It does not cover RazorpayX payouts, disputes, or live money movement.

## Decision

ReRoute must keep provider state, a Customer statement, and money-return evidence separate.

- A Razorpay payment with current API `status: failed` is a confirmed provider failure at the time of the fetch. It is not an irreversible failure. Razorpay can later move the same payment to `authorized` or `captured`.
- A payment in `created` has no bank result yet. A payment in `authorized` has bank approval but is not captured. Only `captured` confirms successful collection for fulfilment.
- A Customer statement that their account was debited is not confirmed by a payment field or webhook. It should open or update a PaymentException and pause duplicate collection while ReRoute checks provider state.
- An issuing-bank reversal after a failed payment has no documented Razorpay webhook, entity, or completion field. ReRoute cannot claim that the money returned unless the Customer or bank supplies that evidence.
- A refund is observable because Razorpay creates a separate refund entity. Its `pending`, `processed`, or `failed` state must remain distinct from a failed-payment bank reversal.

Test Mode can exercise the provider-state branches and webhook handling. It cannot prove a Customer debit, issuing-bank reversal, or bank-account credit because Test Mode moves no real money. Razorpay states that Test and Live webhook payloads have the same structure, while its checkout testing guide states that Test Mode uses simulated payments and deducts no money ([Validate and Test Webhooks](https://razorpay.com/docs/webhooks/validate-test/#test-webhooks), [Standard Checkout test integration](https://razorpay.com/docs/payments/payment-gateway/web-integration/standard/integration-steps/#2-test-integration)). Every displayed amount or result from this flow therefore needs the `TEST MODE` ClaimTag.

## Signals ReRoute can use

### Payment webhooks

The current payment webhook documentation lists these state events ([Payments Webhook Events](https://razorpay.com/docs/webhooks/payments/)):

- `payment.failed` carries a payment snapshot whose `status` is `failed`, `captured` is false, and error fields describe the reported failure.
- `payment.authorized` carries the snapshot from authorization. The payment may already have moved to `captured` when delivery occurs, so the event payload is historical evidence rather than the current entity state.
- `payment.captured` carries a payment snapshot whose `status` is `captured` and `captured` is true.

There is no documented `payment.pending` or payment-reversal event. Absence of a webhook is not a state. For an unresolved or time-sensitive Customer flow, ReRoute should fetch the current payment with `GET /v1/payments/:id` ([Fetch a Payment With ID](https://razorpay.com/docs/api/payments/fetch-with-id/)).

Razorpay delivers webhooks asynchronously with at-least-once semantics, may deliver them out of order, and retries failed deliveries for 24 hours. The `x-razorpay-event-id` header identifies duplicates ([Webhook Best Practices](https://razorpay.com/docs/webhooks/best-practices/), [Order of Webhooks](https://razorpay.com/docs/webhooks/validate-test/#order-of-webhooks)). ReRoute must not treat arrival order as payment chronology. On a contradictory sequence, it should retain each PaymentEvent and fetch the current entity before changing a RecoveryCase or PaymentException.

### Payment API fields

The payment entity exposes these useful fields ([Payments Entity](https://razorpay.com/docs/api/payments/entity/)):

| Field | What it establishes | What it does not establish |
| --- | --- | --- |
| `id`, `order_id` | The payment attempt and its merchant order. | That another attempt for the same order did not succeed. |
| `status` | Current Razorpay state: `created`, `authorized`, `captured`, `refunded`, or `failed`. | A Customer's bank-statement state. |
| `captured` | Whether Razorpay captured this payment. | That a later refund reached the Customer's account. |
| `error_code`, `error_description`, `error_source`, `error_step`, `error_reason` | Razorpay's failure details when present. | Whether the Customer was debited or whether a debit was reversed. |
| `amount_refunded`, `refund_status` | Amount refunded and `null`, `partial`, or `full` refund state on the payment. | Refund processing status or the reason and initiator for the refund. |
| `acquirer_data` | Method-specific bank references when Razorpay has them. | Proof that the Customer saw a debit or credit. The failed-payment webhook sample has a null `bank_transaction_id`. |
| `created_at` | When Razorpay created the payment. | When each later state transition occurred. |

`created` is the only documented payment state that represents a payment with no later result yet. It should be shown as checking or unresolved, not failed. `authorized` means the bank approved the payment, but the merchant still needs capture. Razorpay tells merchants to fulfil only after `captured` ([Standard Checkout payment status](https://razorpay.com/docs/payments/payment-gateway/web-integration/standard/integration-steps/#16-verify-payment-status)).

### Refund webhooks and fields

Refunds have their own `rfnd_` entity and link back through `payment_id`. Current refund webhooks are `refund.created`, `refund.processed`, `refund.failed`, and `refund.speed_changed` ([Refunds Webhook Events](https://razorpay.com/docs/webhooks/refunds/)). The event name and embedded state are separate facts. Razorpay's `refund.created` sample already contains `status: processed`, so `refund.created` does not mean the refund is still pending.

The refund entity exposes `id`, `payment_id`, `amount`, `status`, `created_at`, `speed_requested`, `speed_processed`, and `acquirer_data`. Its documented states are ([Refunds Entity](https://razorpay.com/docs/api/refunds/entity/)):

- `pending`: Razorpay is attempting to process the refund.
- `processed`: Razorpay's final refund state.
- `failed`: Razorpay could not process the refund.

`refund.processed` is the best provider confirmation that Razorpay processed the refund. It still does not prove that the credit is visible in the Customer's account. Razorpay says a normal refund can take 7 to 10 business days to appear, and it may mark a refund `processed` before receiving the ARN or RRN unless the merchant enables stricter gateway confirmation ([Normal Refunds](https://razorpay.com/docs/payments/refunds/normal/#processing-time), [About Refunds](https://razorpay.com/docs/payments/refunds/#refund-states)). When present, `acquirer_data.arn`, RRN, or UTR gives the Customer a reference to trace with the bank ([Customer Refunds](https://razorpay.com/docs/payments/customers/customer-refunds/#refund-communication)).

## Event sequences and interpretation

### Provider failure with no later success

Expected evidence:

1. The payment is `created` while Razorpay lacks the bank result.
2. Razorpay may mark it `failed` after 10 minutes without a response.
3. `payment.failed` may arrive, and a fresh payment fetch returns `status: failed` with failure fields.
4. Razorpay polls the bank for three days. If no later provider state appears, ReRoute can say that Razorpay still reports the payment as failed.

Razorpay documents the 10-minute timeout and three-day polling window ([Late Payment Authorisations](https://razorpay.com/docs/payments/payments/late-authorisation/#how-late-authorisations-are-handled)). It does not document a `failure.finalized` event or field. ReRoute should therefore avoid "permanently failed." During the polling window, the merchant interface should show "failed, late success still possible." After that window it can show "Razorpay still reports failed," with the last verification time.

### Pending result or late success

There are two distinct pending conditions:

- `created` means Razorpay has no bank result. The Customer and merchant interfaces should show "checking payment" and suppress another collection attempt until a fetch resolves it or Policy allows a reviewed override.
- `authorized` means the bank approved the payment, but capture is pending. It is not a failed payment and should not remain in a RecoveryCase asking for payment again.

A late success is established by a later `payment.authorized` or `payment.captured` state for a payment previously observed as failed. Razorpay explicitly documents `payment.failed` followed by `payment.captured` for the same payment due to late authorization or a Customer retry in a UPI app ([Payment Captured webhook warning](https://razorpay.com/docs/webhooks/payments/#payment-captured)). Because delivery can be out of order, ReRoute should confirm the current state with the Payment API before calling it late success.

If the latest state is `authorized`, the result is approved but not collected. Capture settings decide whether Razorpay captures or automatically refunds it. Uncaptured authorized payments must be captured within three days and are otherwise automatically refunded ([Payment Capture Settings](https://razorpay.com/docs/payments/payments/capture-settings/)). If the latest state is `captured`, stop collection Actions and record the provider-backed Outcome only once the payment belongs to the obligation ReRoute was trying to recover.

### Customer-reported debit

The sequence is:

1. Razorpay reports `created` or `failed`.
2. The Customer says their account was debited.
3. ReRoute records that statement as Customer evidence in a PaymentException, not as a PaymentEvent, refund, or Outcome.
4. ReRoute fetches the payment and watches for `authorized` or `captured` while keeping duplicate collection paused.

Razorpay says late authorization can occur when funds may or may not have been debited and Razorpay has no bank status ([Late Payment Authorisations](https://razorpay.com/docs/payments/payments/late-authorisation/)). No documented payment field confirms the debit. A bank reference or screenshot can support an investigation, but it does not change provider state by itself.

### Automatic reversal after a failed debit

For a failed payment where the Customer was debited, Razorpay says the issuing bank auto-refunds the amount in 7 to 10 working days ([Payments FAQ, question 12](https://razorpay.com/docs/payments/payments/faqs/#12-a-payment-is-marked-as-failed-on)). This is bank-side handling of the failed debit. Razorpay does not document a webhook, refund entity, payment field, or reference that confirms completion of this reversal.

ReRoute can show "bank reversal expected" with the provider's stated window. It cannot show "reversed" or "money returned" without Customer or bank evidence. If no credit appears after the stated window, the interface should direct the Customer to support and retain the PaymentException. This path must not create a refund record unless Razorpay supplies an actual `rfnd_` entity.

An uncaptured late-authorized payment is a different automatic path. Razorpay says it auto-refunds such payments in five days, excluding bank processing time ([Handle Late Authorised Payments](https://razorpay.com/docs/payments/payments/late-authorisation/handle/)). Once Razorpay exposes a refund entity, ReRoute should follow the refund path below. The documented refund fields do not identify who initiated the refund or why, so provider data alone cannot distinguish capture-timeout auto-refund from a merchant-initiated refund.

### Refund

The observable sequence is:

1. Razorpay creates a refund entity linked by `payment_id`; `refund.created` may arrive.
2. The refund may be `pending` while Razorpay or the banking network processes it.
3. `refund.processed` with entity `status: processed` confirms Razorpay's final provider state, or `refund.failed` with `status: failed` confirms failure.
4. The payment's `amount_refunded` and `refund_status` show aggregate partial or full refund progress.
5. The Customer may still wait for the bank credit after `processed`. Show the ARN, RRN, or UTR when `acquirer_data` contains it.

The merchant interface should show refund amount, refund ID, provider status, speed, bank reference, and last update. The Customer interface should say "refund processed by Razorpay" rather than "money received" until the Customer confirms the credit. A `pending` or failed refund remains a PaymentException concern and must never count as Actual Recovered.

## Required uncertainty in both interfaces

Both interfaces need the current Razorpay state, its source, the last verification time, and a `TEST MODE` ClaimTag. They must preserve these distinctions:

| Situation | Customer wording | Merchant wording |
| --- | --- | --- |
| Payment `created` or webhook missing | Checking payment. Do not pay again yet. | Provider result unresolved. API verification due or in progress. |
| Current payment `failed`, no debit report | Payment did not complete. | Razorpay reports failed. Late success remains possible during provider checking. |
| Customer reports debit, provider not successful | Debit reported. We are checking it. | PaymentException. Customer claim is unverified by Razorpay; pause duplicate collection. |
| Payment becomes `authorized` | Payment approved. Confirmation is still in progress. | Late or normal authorization. Capture or auto-refund decision pending. |
| Payment becomes `captured` | Payment confirmed. | Captured payment. Stop collection Actions and reconcile the obligation. |
| Failed debit awaiting bank reversal | Your bank is expected to return the amount. The credit is not confirmed yet. | Issuer reversal expected. No Razorpay completion signal exists. |
| Refund `pending` | Refund is being processed. | Refund entity pending. Do not describe it as credited. |
| Refund `processed`, no Customer confirmation | Razorpay processed the refund. Your bank may still take time to show it. | Provider refund processed. Bank credit is not independently confirmed. |
| Refund `failed` | Refund could not be processed. Support is reviewing it. | Refund failed. PaymentException remains open for intervention. |

Neither interface can infer the following from Razorpay provider data alone:

- whether a Customer actually saw a debit on a `created` or `failed` payment;
- whether an issuing-bank reversal reached the Customer;
- when a processed normal refund will appear in the Customer's account;
- whether an automatic refund was caused by capture timeout or merchant action;
- whether a failed state will never become authorized or captured;
- whether a different payment attempt for the same order already succeeded, unless ReRoute also reconciles order attempts.

## Fit with the current ReRoute code

The default branch only normalizes `payment.failed` and `payment.captured` (`app/domain/enums.py`, `app/domain/models.py`). It cannot represent `created`, `authorized`, or refund events. The state machine opens a RecoveryCase immediately on `payment.failed` and marks it recovered on `payment.captured` (`app/domain/state_machine.py`). It has no PaymentException persistence yet, despite the domain definition in `CONTEXT.md`.

The normalizer also uses the payment entity's `created_at` as `PaymentEvent.occurred_at`. That timestamp is the payment creation time, not the state-event time. Razorpay webhook envelopes have their own top-level `created_at` in the documented samples. Event ordering work will need to retain both times. The current endpoint does not read the documented `x-razorpay-event-id` header, which Razorpay recommends for webhook deduplication.

These are later implementation decisions. This research ticket changes no application code.
