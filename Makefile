.PHONY: help sync test lint typecheck jscheck verify browser-check \
	genuine-prepare genuine-probe genuine-preflight genuine-evidence genuine-session \
	genuine-webhook-start genuine-webhook-status genuine-webhook-stop openrouter-smoke \
	demo-start demo-start-with-credentials demo-status demo-stop demo-restart demo-open demo-logs demo-public-status

help:
	@printf '%s\n' \
		'make sync                    Install development dependencies' \
		'make test                    Run all Python tests' \
		'make lint                    Run Ruff' \
		'make typecheck               Run mypy' \
		'make jscheck                 Syntax-check dashboard JavaScript' \
		'make verify                  Run normal release verification' \
		'make browser-check           Run local dashboard DOM test' \
		'make genuine-prepare         Prepare Test Mode environment; requires CREDENTIALS=...' \
		'make genuine-session         Start a complete local Test Mode proof session; requires CREDENTIALS=...' \
		'make genuine-probe           Run genuine provider order probe; requires CREDENTIALS=...' \
		'make genuine-preflight       Check Test Mode/public URL readiness; requires PUBLIC_URL=...' \
		'make genuine-evidence        Print sanitized local provider evidence' \
		'make genuine-webhook-start   Start zrok and verify the public webhook fails closed' \
		'make genuine-webhook-status  Show tunnel configuration and signed provider evidence' \
		'make genuine-webhook-stop    Stop only the ReRoute zrok share process' \
		'make openrouter-smoke        Verify one bounded OpenRouter FindingAnalysis' \
		'make demo-start              Start the local ReRoute demo' \
		'make demo-status             Show local ReRoute runtime status' \
		'make demo-open               Open dashboard and storefront' \
		'make demo-logs               Show recent local server logs' \
		'make demo-stop               Stop the local ReRoute demo'

sync:
	uv sync --dev

test:
	uv run pytest -q

lint:
	uv run ruff check .

typecheck:
	uv run mypy

jscheck:
	node --check app/static/js/api.js
	node --check app/static/js/app.js
	node --check app/static/js/dashboard-view.js
	node --check app/static/js/dashboard-format.js
	node --check app/static/js/recovery-workflow.js

verify: test lint typecheck jscheck
	git diff --check

browser-check:
	bash tests/browser/dashboard-dom.test.sh

genuine-prepare:
	@test -n "$(CREDENTIALS)" || (echo "CREDENTIALS path is required" && exit 2)
	uv run python scripts/genuine_testmode_prepare.py \
		--credentials-file "$(CREDENTIALS)"

genuine-session:
	@test -n "$(CREDENTIALS)" || (echo "CREDENTIALS path is required" && exit 2)
	uv run python scripts/genuine_testmode_session.py \
		--credentials-file "$(CREDENTIALS)"

genuine-probe:
	@test -n "$(CREDENTIALS)" || (echo "CREDENTIALS path is required" && exit 2)
	uv run python scripts/genuine_testmode_provider_probe.py \
		--credentials-file "$(CREDENTIALS)" \
		--reset-db

genuine-preflight:
	@test -n "$(PUBLIC_URL)" || (echo "PUBLIC_URL is required" && exit 2)
	@set -a; \
	. .reroute-local/genuine-testmode.env; \
	set +a; \
	uv run python scripts/live_testmode_preflight.py \
		--public-url "$(PUBLIC_URL)"

genuine-evidence:
	uv run python scripts/genuine_testmode_evidence.py \
		--base-url "$${BASE_URL:-http://127.0.0.1:8000}"

genuine-webhook-start:
	uv run python scripts/genuine_testmode_webhook.py start

genuine-webhook-status:
	uv run python scripts/genuine_testmode_webhook.py status

genuine-webhook-stop:
	uv run python scripts/genuine_testmode_webhook.py stop

openrouter-smoke:
	uv run python scripts/openrouter_smoke.py \
		--base-url "$${BASE_URL:-http://127.0.0.1:8000}"

demo-start:
	uv run python scripts/demo_runtime.py start

demo-start-with-credentials:
	@test -n "$(CREDENTIALS)" || (echo "CREDENTIALS path is required" && exit 2)
	uv run python scripts/demo_runtime.py start \
		--credentials-file "$(CREDENTIALS)"

demo-status:
	uv run python scripts/demo_runtime.py status

demo-stop:
	uv run python scripts/demo_runtime.py stop

demo-restart:
	uv run python scripts/demo_runtime.py restart

demo-open:
	uv run python scripts/demo_runtime.py open

demo-logs:
	uv run python scripts/demo_runtime.py logs

demo-public-status:
	uv run python scripts/demo_runtime.py public-status
