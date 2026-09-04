#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$root"
scratch="$root/.chrome-scratch/sentinel-visual-${$}"
port=$((17000 + ($$ % 1000)))
chrome_port=$((19000 + ($$ % 1000)))
db="$scratch/sentinel.db"
screenshots="${SENTINEL_SCREENSHOT_DIR:-$scratch/screenshots}"
mkdir -p "$scratch" "$screenshots"

cleanup() {
  if [[ -n "${browser_pid:-}" ]]; then
    kill "$browser_pid" 2>/dev/null || true
    wait "$browser_pid" 2>/dev/null || true
  fi
  if [[ -n "${server_pid:-}" ]]; then
    kill "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
  fi
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

cat >"$scratch/visual_app.py" <<'PY'
import os

from app.integrations.razorpay import PaymentLinkReference
from app.main import create_app


class OfflineIncidentProvider:
    requested_model = "browser-fallback"

    def generate(self, snapshot):
        raise ValueError("browser test uses deterministic analysis fallback")


def create_payment_link(amount: int, idempotency_key: str):
    return PaymentLinkReference(
        f"https://rzp.test/recovery/{idempotency_key}",
        f"plink_browser_{idempotency_key}",
    )


app = create_app(
    database_url=os.environ["REROUTE_DATABASE_URL"],
    create_payment_link=create_payment_link,
    razorpay_key_id="rzp_test_browser",
    incident_analysis_provider=OfflineIncidentProvider(),
    sentinel_owner_actor_id="browser_business_owner",
)
PY

PYTHONPATH="$root:$scratch" REROUTE_DATABASE_URL="sqlite:///$db" \
  uv run uvicorn visual_app:app --app-dir "$scratch" --host 127.0.0.1 --port "$port" --log-level warning \
  >"$scratch/server.log" 2>&1 &
server_pid=$!
for _ in {1..80}; do
  if curl --silent --fail "http://127.0.0.1:$port/health" >/dev/null; then
    break
  fi
  sleep 0.1
done
curl --silent --fail "http://127.0.0.1:$port/health" >/dev/null

curl --silent --fail -X POST "http://127.0.0.1:$port/api/v1/replay/run?scenario=primary" >/dev/null
incident_count=$(curl --silent --fail "http://127.0.0.1:$port/api/v1/incidents" | uv run python -c 'import json,sys; print(len(json.load(sys.stdin)))')
if [[ "$incident_count" -lt 1 ]]; then
  echo "merchant replay did not produce an incident" >&2
  exit 1
fi

chrome_bin=""
for candidate in chromium chromium-browser google-chrome google-chrome-stable; do
  if command -v "$candidate" >/dev/null 2>&1; then
    chrome_bin=$(command -v "$candidate")
    break
  fi
done
if [[ -z "$chrome_bin" ]]; then
  echo "No Chromium-compatible browser is installed" >&2
  exit 1
fi

"$chrome_bin" --headless --no-sandbox --disable-gpu --disable-dev-shm-usage \
  --remote-allow-origins=* --remote-debugging-port="$chrome_port" \
  --user-data-dir="$scratch/chrome" about:blank >"$scratch/chrome.log" 2>&1 &
browser_pid=$!
for _ in {1..80}; do
  if curl --silent --fail "http://127.0.0.1:$chrome_port/json/version" >/dev/null; then
    break
  fi
  sleep 0.1
done
curl --silent --fail "http://127.0.0.1:$chrome_port/json/version" >/dev/null

SENTINEL_BASE_URL="http://127.0.0.1:$port" \
SENTINEL_CHROME_PORT="$chrome_port" \
SENTINEL_SCREENSHOT_DIR="$screenshots" \
node tests/browser/sentinel-browser-test.mjs

printf 'Browser screenshots written to %s\n' "$screenshots"
