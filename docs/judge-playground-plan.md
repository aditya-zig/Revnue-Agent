# Judge Playground Rollout

Goal. Give judges one public HTTPS link where they can inspect the ReRoute dashboard, create the 999-payment historical population, open the 5 kg dumbbell storefront, and inspect the recovery workflow.

## Done in this branch

- Added `/judge` as the public landing page for the evaluation flow.
- Added a Render Blueprint for the existing FastAPI application.
- Kept the existing dashboard and storefront as the real product surfaces. No duplicate demo UI was created.
- Kept Razorpay credentials and webhook secret as deployment-only environment variables.
- Added a route contract test for the judge landing page.
- Preserved evidence labels. SIMULATED is historical benchmark data. ESTIMATED is persisted leak impact. TEST MODE is only used for Razorpay Test Mode evidence.

## Public flow

1. Judge opens `/judge`.
2. Judge opens the dashboard.
3. Judge clicks `Simulate 999 Payments` on a clean deployment.
4. Judge inspects the leak finding, policy boundary, ranked recovery actions, worklist, and evaluation.
5. Judge opens `/storefront` and tries payment #1000 through official Razorpay Checkout when Test Mode credentials are configured.
6. Razorpay Test Mode webhooks are configured to `https://<public-host>/api/v1/webhooks/razorpay`.
7. Judge returns to the dashboard to inspect provider evidence, case state, human approval, action timeline, and outcome.

## Deployment boundary

The repository is deployment-ready through `render.yaml`, but the hosting account must still create the service and add deployment secrets. Do not put Razorpay values in GitHub, this document, logs, screenshots, or chat.

Required deployment environment variables are `REROUTE_RAZORPAY_KEY_ID`, `REROUTE_RAZORPAY_KEY_SECRET`, and `REROUTE_RAZORPAY_WEBHOOK_SECRET`. `REROUTE_OPENROUTER_API_KEY` is optional because FindingAnalysis has a deterministic fallback.

The current Blueprint uses SQLite under `/tmp`. That is intentionally disposable for the hackathon demo. A service restart can reset the database. Before submission, rehearse from a fresh deployment and ensure the public link is awake.

## Remaining finalization

- Create the public hosting service from `render.yaml`.
- Add Test Mode secrets in the hosting dashboard.
- Verify `/health`, `/judge`, `/`, and `/storefront` through the public HTTPS URL.
- Configure Razorpay Test Mode webhook delivery to the public URL.
- Run the genuine Test Mode evidence path and keep external delivery claims separate from local signed-fixture evidence.
- Test the exact judge flow from an incognito browser.
- Record the public URL in the submission only after the rehearsal passes.
