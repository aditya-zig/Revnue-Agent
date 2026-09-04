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

wait_for_url() {
  local url=$1
  local log_file=$2
  local label=$3
  for _ in {1..120}; do
    if curl --silent --fail "$url" >/dev/null; then
      echo "$label ready"
      return 0
    fi
    sleep 0.1
  done
  echo "$label failed to become ready at $url" >&2
  if [[ -f "$log_file" ]]; then
    echo "----- $label log -----" >&2
    cat "$log_file" >&2 || true
    echo "----- end $label log -----" >&2
  fi
  return 1
}

wait_for_browser() {
  local url=$1
  local log_file=$2
  echo "polling DevTools URL: $url"
  for _ in {1..200}; do
    if curl --silent --fail "$url" >/dev/null; then
      echo "Chromium DevTools ready"
      return 0
    fi
    if ! kill -0 "$browser_pid" 2>/dev/null; then
      set +e
      wait "$browser_pid"
      local status=$?
      set -e
      echo "browser exited before DevTools became ready (status=$status)" >&2
      echo "browser process alive: no" >&2
      echo "browser pid: $browser_pid" >&2
      echo "selected browser: $chrome_bin" >&2
      echo "browser version: $browser_version" >&2
      echo "----- Chromium startup log -----" >&2
      cat "$log_file" >&2 || true
      echo "----- end Chromium startup log -----" >&2
      return 1
    fi
    sleep 0.1
  done
  echo "Chromium DevTools failed to become ready at $url" >&2
  echo "browser process alive: $(kill -0 "$browser_pid" 2>/dev/null && echo yes || echo no)" >&2
  echo "browser pid: $browser_pid" >&2
  echo "selected browser: $chrome_bin" >&2
  echo "browser version: $browser_version" >&2
  ps -o pid,ppid,stat,etime,cmd -p "$browser_pid" >&2 || true
  echo "----- Chromium startup log -----" >&2
  cat "$log_file" >&2 || true
  echo "----- end Chromium startup log -----" >&2
  return 1
}

uv run python - "$db" <<'PY'
import sys
from pathlib import Path
from alembic import command
from alembic.config import Config

config = Config("alembic.ini")
config.set_main_option("sqlalchemy.url", f"sqlite:///{Path(sys.argv[1])}")
command.upgrade(config, "head")
PY

echo "database migrated"

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
  uv run uvicorn visual_app:app --app-dir "$scratch" --host 127.0.0.1 --port "$port" --log-level info \
  >"$scratch/server.log" 2>&1 &
server_pid=$!
wait_for_url "http://127.0.0.1:$port/health" "$scratch/server.log" "Sentinel test server"

echo "starting merchant replay"
curl --silent --show-error --fail -X POST "http://127.0.0.1:$port/api/v1/replay/start" >"$scratch/replay-start.json"
cat "$scratch/replay-start.json"

active_incident=""
for step in {1..50}; do
  curl --silent --show-error --fail -X POST \
    "http://127.0.0.1:$port/api/v1/replay/advance?count=6&scenario=primary" \
    >"$scratch/replay-advance.json"
  active_incident=$(curl --silent --show-error --fail "http://127.0.0.1:$port/api/v1/incidents" | uv run python -c '
import json, sys
rows = json.load(sys.stdin)
active = [row for row in rows if row.get("state") not in {"resolved"}]
print(active[0]["incident_id"] if active else "")
')
  if [[ -n "$active_incident" ]]; then
    echo "active incident found after replay step $step: $active_incident"
    break
  fi
done

if [[ -z "$active_incident" ]]; then
  echo "merchant replay never entered an active incident window" >&2
  cat "$scratch/replay-advance.json" >&2 || true
  cat "$scratch/server.log" >&2 || true
  exit 1
fi

curl --silent --show-error --fail "http://127.0.0.1:$port/api/v1/incidents/$active_incident" >"$scratch/incident.json"
cat "$scratch/incident.json"

chrome_bin=""
for candidate in google-chrome-stable google-chrome chromium chromium-browser; do
  candidate_path=$(command -v "$candidate" 2>/dev/null || true)
  if [[ -n "$candidate_path" ]]; then
    echo "browser candidate: $candidate -> $candidate_path"
    if [[ -z "$chrome_bin" ]]; then
      chrome_bin=$candidate_path
    fi
  fi
done
if [[ -z "$chrome_bin" ]]; then
  echo "No Chromium-compatible browser is installed" >&2
  exit 1
fi
browser_version=$($chrome_bin --version 2>&1 || true)
echo "selected browser: $chrome_bin"
echo "browser version: $browser_version"

"$chrome_bin" --headless=new --no-sandbox --disable-gpu --disable-dev-shm-usage \
  --remote-allow-origins=* --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port="$chrome_port" --user-data-dir="$scratch/chrome" \
  --no-first-run --no-default-browser-check about:blank >"$scratch/chrome.log" 2>&1 &
browser_pid=$!
echo "browser pid: $browser_pid"
wait_for_browser "http://127.0.0.1:$chrome_port/json/version" "$scratch/chrome.log"

echo "starting browser journey"
SENTINEL_BASE_URL="http://127.0.0.1:$port" \
SENTINEL_CHROME_PORT="$chrome_port" \
SENTINEL_SCREENSHOT_DIR="$screenshots" \
node tests/browser/sentinel-browser-test.mjs

printf 'Browser screenshots written to %s\n' "$screenshots"
