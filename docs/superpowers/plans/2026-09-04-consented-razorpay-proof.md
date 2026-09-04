# Consented Razorpay Test-Mode Recovery Proof

## Objective

Make the production storefront's explicit customer consent enable the `payment_link` policy path, create a real Razorpay Test Mode Payment Link after approval, and mark recovery only after an authenticated matching `payment.captured` webhook.

## Steps

1. Trace storefront consent, policy evaluation, recovery action execution, and webhook correlation; identify the smallest root cause.
2. Add focused failing integration/static tests covering consent persistence, `payment_link` eligibility/recommendation, provider-link creation, and captured-event reconciliation.
3. Implement the minimal fix without changing unrelated product behavior or introducing a real retry executor.
4. Run targeted tests, then the full test/lint/type/syntax checks and inspect the diff.
5. Deploy/verify the merged path if deployment access is available; report exact provider-delivery and recovery evidence status.
