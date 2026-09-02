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

## Genuine Razorpay Test Mode

- [x] Genuine Test Mode order confirmed with Razorpay API
- [ ] Public HTTPS endpoint available
- [ ] Test Mode webhook configured
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
- [x] README
- [x] Five-minute demo runbook
- [x] Provider-proof branch tooling
- [ ] Provider-proof branch merged
- [x] GitHub CI configured
- [ ] Issue #47 closed after proof

## Recording

- [ ] Start from clean demo DB
- [ ] Simulate 999 Payments
- [ ] Show 999 / 749 / 250 / 25.03%
- [ ] Show top leak
- [ ] Show hard-decline safety
- [ ] Open storefront
- [ ] Produce payment #1000
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
