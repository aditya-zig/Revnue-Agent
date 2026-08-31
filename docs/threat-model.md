# Threat model

## Scope and assets

The prototype handles synthetic payment-event records and, when configured,
Razorpay Test Mode webhook data. The important assets are the webhook secret,
event integrity, action permissions, idempotency records, audit history, and
the local database.

## Threats and controls

| Threat | Control in this repository | Remaining gap |
| --- | --- | --- |
| Forged webhook | `app/core/security.py` checks the Razorpay HMAC SHA-256 signature against the raw request body. | Operators must store and rotate the Test Mode secret outside Git. |
| Replay or duplicate delivery | Payment events use a unique provider identity. Action requests use an idempotency key and handle an insert race. | Idempotency retention and replay monitoring need production policy. |
| Out-of-order payment events | A later capture marks the case recovered and cancels pending actions. | Delivery ordering still depends on provider data quality. |
| Unauthorized customer contact | Consent, identity, quiet hours, a three-contact cap, and a 24-hour action limit block actions in policy. | The prototype has no authenticated operator identity; the business-owner role header is not authentication. |
| Unsafe automated choice | The policy filters actions before scoring. Structured model output has an allowlist and rejects extra fields. The default uses deterministic fallback. | Policy rules need merchant review before use with real data. |
| Provider outage | A payment-link exception writes `action.failed`, assigns the case to the business owner, and returns HTTP 502. A business owner can resume it after a current policy check. | Automatic retries and alerts are absent. |
| Resource exhaustion | Request bodies have a configured maximum size. | No rate limiting, authentication, or production ingress controls exist. |
| Data disclosure | `.env`, databases, Python caches, virtual environments, and `rzp-test-key.csv` are ignored. Razorpay Test Mode keys live in `~/.razorpay/config.yaml` and `RAZORPAY_BASIC_TOKEN` in the shell, not in the repo. The committed demo records have synthetic identifiers. | Local SQLite encryption, access control, retention, and deletion are not implemented. |

This is a prototype threat model, not a production security approval. A public
deployment needs authentication, authorization, secret management, encrypted
storage, rate limiting, observability, incident response, and a privacy review.
