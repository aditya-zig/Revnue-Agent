# ReRoute submission checklist

## Application

- [x] Deterministic 999-payment merchant history
- [x] 749 captured / 250 failed
- [x] Detector-generated LeakFindings
- [x] Dashboard population metrics
- [x] Hard-decline Policy safety proof
- [x] RecoveryModel ranks only permitted actions
- [x] Human approval boundary
- [x] Action audit trail
- [x] Controlled Test Mode-format recovery Outcome
- [x] 5 kg Dumbbell storefront
- [x] Official Razorpay Checkout integration
- [x] Raw-body webhook signature validation
- [x] Duplicate webhook protection
- [x] CheckoutOrder correlation
- [x] Claim-tag separation
- [x] OpenRouter advisory boundary implemented with deterministic fallback
- [ ] OpenRouter configured smoke check returns `external_model_generated=true`

## Genuine Razorpay Test Mode

- [x] Genuine Test Mode order confirmed with Razorpay API
- [x] Public webhook tunnel start/status/stop tooling
- [x] Public invalid-signature fail-closed check implemented
- [ ] Public HTTPS endpoint available in the final session
- [ ] Test Mode webhook configured in Razorpay Dashboard
- [ ] `payment.failed` subscribed
- [ ] `payment.captured` subscribed
- [ ] Official Checkout failure performed
- [ ] Provider webhook delivery observed
- [ ] ReRoute accepted genuine `payment.failed`
- [ ] Exact RecoveryCase correlated
- [ ] Genuine recovery Payment Link created
- [ ] Genuine `payment.captured` observed
- [ ] `RECOVERED` Outcome persisted

## Repository

- [x] Controlled demo merged to main
- [x] Secret scan
- [x] README rebuilt for submission
- [x] Apache-2.0 license
- [x] Five-minute demo runbook
- [x] Provider-proof branch tooling
- [x] GitHub CI configured
- [ ] Final screenshots added to README
- [ ] Final five-minute video linked from README
- [ ] Provider-proof branch merged
- [ ] Issue #47 closed after proof

## Screenshots

- [ ] `docs/assets/dashboard-overview.png` — 999 / 749 / 250, top leak, executive metrics
- [ ] `docs/assets/payment-1000-case.png` — exact payment #1000 RecoveryCase
- [ ] `docs/assets/policy-ranking.png` — Policy-permitted actions, ranking, recommendation, approval
- [ ] `docs/assets/storefront-checkout.png` — 5 kg Dumbbell with official Razorpay Test Mode Checkout

## Recording

- [ ] Start from clean demo DB
- [ ] Show 999 / 749 / 250 / 25.03%
- [ ] Show top leak
- [ ] Show hard-decline safety
- [ ] Open storefront
- [ ] Produce payment #1000
- [ ] Show provider evidence honestly
- [ ] Show exact RecoveryCase
- [ ] Investigate
- [ ] Show Policy boundary
- [ ] Show ranked actions
- [ ] Approve recommended action
- [ ] Show outcome
- [ ] Show evaluation as SIMULATED
- [ ] Keep video at or below five minutes

## Claims

Never describe:

- synthetic evaluation as merchant lift
- fixture webhooks as provider-delivered
- Test Mode recovered amount as production revenue
- browser callbacks as authoritative payment evidence
- a locally accepted signed webhook as provider-delivered without separate Razorpay provider evidence
