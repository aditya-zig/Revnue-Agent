# Vercel + Supabase

ReRoute can run on Vercel with Supabase Postgres.

## 1. Create the Vercel project first

You do not need the final webhook URL before creating the project. Import the repository and deploy only after the database variables are ready. Vercel environment variables can be edited after project creation and after deployment.

## 2. Supabase database URL

In Supabase, open **Connect** and copy the **Transaction pooler** connection string for serverless application traffic. It normally uses port `6543`.

Set that complete connection string as:

```text
REROUTE_DATABASE_URL=...
```

The backend does not need a Supabase publishable key. The database password is already part of the connection string.

ReRoute accepts either `postgres://...` or `postgresql://...` and normalizes the former for SQLAlchemy.

## 3. Required Vercel variables

```text
REROUTE_DATABASE_URL
REROUTE_RAZORPAY_KEY_ID
REROUTE_RAZORPAY_KEY_SECRET
REROUTE_RAZORPAY_WEBHOOK_SECRET
REROUTE_OPENROUTER_API_KEY
```

Do not commit real values.

## 4. Initialize the hosted database

After `REROUTE_DATABASE_URL` is available locally, run once:

```bash
export REROUTE_DATABASE_URL='YOUR_SUPABASE_CONNECTION_STRING'
make db-migrate
```

This applies the existing Alembic migrations. Do not create the tables manually in the Supabase dashboard.

## 5. Razorpay webhook

After Vercel gives the final production URL, configure the Razorpay **Test Mode** webhook as:

```text
https://YOUR-VERCEL-DOMAIN/api/v1/webhooks/razorpay
```

Subscribe to:

```text
payment.failed
payment.captured
```

Generate a strong webhook secret yourself and use the exact same value in Razorpay and Vercel as `REROUTE_RAZORPAY_WEBHOOK_SECRET`.

## 6. Verification

After deployment:

```text
GET /health
GET /judge
GET /
GET /storefront
```

Then run the 999-payment simulation from the dashboard and confirm the state persists across page refreshes before sharing the judge URL.
