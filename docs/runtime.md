# ReRoute runtime map

```text
USER
 |
 v
make demo-start
 |
 v
scripts/demo_runtime.py
 |
 v
scripts/genuine_testmode_session.py
 |
 +--> private credential preparation
 +--> Alembic migration
 +--> SQLite genuine-demo.db
 +--> Uvicorn / FastAPI
        +--> Dashboard /
        +--> Storefront /storefront
        +--> APIs /api/v1/*
        +--> Webhook /api/v1/webhooks/razorpay
        +--> static JS/CSS
 |
 +--> deterministic simulate-999 history
 +--> Razorpay Test Mode order proof
```

One FastAPI process serves the dashboard, storefront, APIs, webhook receiver,
recovery workflow, Policy, RecoveryModel, mock inbox, evaluation endpoints,
and static assets. SQLite is a file, not a separate process. Optional
OpenRouter FindingAnalysis uses outbound HTTP from that FastAPI process.

For genuine provider delivery only, the external path is:

```text
Razorpay -> public HTTPS tunnel -> /api/v1/webhooks/razorpay
```

The tunnel is not needed to start or rehearse the controlled local demo.
