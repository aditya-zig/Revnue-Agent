# Prior art and primary references

This prototype follows payment-platform mechanics rather than claiming a new
recovery method. The references below describe interfaces and operating
constraints that informed the implementation.

- [Razorpay webhook validation and testing](https://razorpay.com/docs/webhooks/validate-test/)
  documents HMAC SHA-256 signatures over the raw request body, duplicate
  deliveries, Test Mode, and possible out-of-order events. ReRoute verifies the
  raw body, deduplicates events, and handles a later capture after a pending
  action.
- [Razorpay Payment Links](https://razorpay.com/docs/payments/payment-links/)
  describes payment links created through the dashboard or API. ReRoute has a
  narrow payment-link adapter seam, but the default provider throws an error.
  The public demo therefore never creates a real link.
- [OWASP Top 10](https://owasp.org/Top10/2025/) is a security awareness
  reference for web applications. The threat model maps this prototype's
  concrete controls and missing production controls without claiming compliance.

The repository does not cite market-size studies, recovery-rate research, or
vendor comparisons. It has no source for those claims, so it does not make
them.
