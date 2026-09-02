#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$root"
scratch="$root/.chrome-scratch/dashboard-dom-${$}"
port=$((18000 + ($$ % 1000)))
chrome_port=$((19000 + ($$ % 1000)))
db="$scratch/dashboard.db"
mkdir -p "$scratch"

cleanup() {
  if [[ -n "${browser_pid:-}" ]]; then
    kill "$browser_pid" 2>/dev/null || true
    wait "$browser_pid" 2>/dev/null || true
  fi
  if [[ -n "${server_pid:-}" ]]; then
    kill "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
  fi
  rm -rf "$scratch"
}
trap cleanup EXIT

uv run python - "$db" <<'PY'
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

config = Config("alembic.ini")
config.set_main_option("sqlalchemy.url", f"sqlite:///{Path(sys.argv[1])}")
command.upgrade(config, "head")
PY

REROUTE_DATABASE_URL="sqlite:///$db" uv run uvicorn app.main:app --host 127.0.0.1 --port "$port" --log-level warning >"$scratch/server.log" 2>&1 &
server_pid=$!
for _ in {1..50}; do
  if curl --silent --fail "http://127.0.0.1:$port/health" >/dev/null; then
    break
  fi
  sleep 0.1
done
curl --silent --fail "http://127.0.0.1:$port/health" >/dev/null

cat >"$scratch/findings.csv" <<'CSV'
event_id,event_type,payment_id,customer_id,amount,currency,method,status,error_code,error_reason,occurred_at,consent,provider
evt_browser_1,payment.failed,pay_browser_1,cust_browser_1,5000,INR,upi,failed,BAD_REQUEST_ERROR,insufficient funds,2026-01-01T00:00:00+00:00,true,csv_import
evt_browser_2,payment.failed,pay_browser_2,cust_browser_2,5000,INR,upi,failed,BAD_REQUEST_ERROR,insufficient funds,2026-01-01T00:01:00+00:00,true,csv_import
evt_browser_3,payment.failed,pay_browser_3,cust_browser_3,5000,INR,upi,failed,BAD_REQUEST_ERROR,insufficient funds,2026-01-01T00:02:00+00:00,true,csv_import
evt_browser_capture_1,payment.captured,pay_browser_capture_1,cust_browser_capture_1,5000,INR,upi,captured,,,2026-01-01T00:03:00+00:00,true,csv_import
evt_browser_capture_2,payment.captured,pay_browser_capture_2,cust_browser_capture_2,5000,INR,upi,captured,,,2026-01-01T00:04:00+00:00,true,csv_import
evt_browser_capture_3,payment.captured,pay_browser_capture_3,cust_browser_capture_3,5000,INR,upi,captured,,,2026-01-01T00:05:00+00:00,true,csv_import
evt_browser_mixed,payment.failed,pay_browser_1,cust_browser_1,5000,INR,upi,failed,BAD_REQUEST_ERROR,insufficient funds,2026-01-01T00:10:00+00:00,true,mock
CSV
curl --silent --fail -X POST "http://127.0.0.1:$port/api/v1/data/import" \
  -H 'Content-Type: text/csv' --data-binary "@$scratch/findings.csv" >/dev/null
curl --silent --fail -X POST "http://127.0.0.1:$port/api/v1/findings/detect" >/dev/null

chromium --headless --no-sandbox --disable-gpu --disable-dev-shm-usage \
  --remote-allow-origins=* --remote-debugging-port="$chrome_port" \
  --user-data-dir="$scratch/chrome" about:blank >"$scratch/chrome.log" 2>&1 &
browser_pid=$!
for _ in {1..50}; do
  if curl --silent --fail "http://127.0.0.1:$chrome_port/json/version" >/dev/null; then
    break
  fi
  sleep 0.1
done
curl --silent --fail "http://127.0.0.1:$chrome_port/json/version" >/dev/null

CHROME_DEVTOOLS_AXI_BROWSER_URL="http://127.0.0.1:$chrome_port" \
  CHROME_DEVTOOLS_AXI_SESSION="dashboard-dom-test-$$" chrome-devtools-axi run <<EOF
await page.open("http://127.0.0.1:$port/");
await page.eval("new Promise((resolve) => setTimeout(resolve, 1000))");
const result = await page.eval("(() => { const policyPanel = [...document.querySelectorAll('#overview .panel')].find((panel) => panel.textContent.includes('Policy signal')); const estimatedBadges = [...(policyPanel?.querySelectorAll('.badge') || [])].filter((badge) => badge.textContent.trim() === 'ESTIMATED'); const openCasesCard = document.querySelector('[data-kpi-slot=\\"open-cases\\"]'); return { estimatedBadges: estimatedBadges.length, openCasesClaimTags: openCasesCard?.querySelectorAll('.claim-tag').length || 0 }; })()");
const providerEvidenceEmpty = await page.eval("(() => [...document.querySelectorAll('#overview .panel')].find((panel) => panel.textContent.includes('Provider evidence'))?.textContent.includes('No signed Razorpay Test Mode webhook evidence is persisted yet.') || false)");
await page.eval("document.querySelector('#tab-queue').click()");
const mixedQueueClaim = await page.eval("(() => { const row = [...document.querySelectorAll('#queue tbody tr')].find((candidate) => candidate.textContent.includes('case_pay_browser_1')); return row ? [...row.querySelectorAll('.case-sub')].some((item) => ['MOCK', 'TEST MODE'].includes(item.textContent.trim())) : true; })()");
if (result.estimatedBadges !== 1 || result.openCasesClaimTags !== 0 || mixedQueueClaim || !providerEvidenceEmpty) {
  throw new Error(JSON.stringify({ ...result, mixedQueueClaim, providerEvidenceEmpty }));
}
console.log(JSON.stringify({ ...result, mixedQueueClaim, providerEvidenceEmpty }));
EOF
