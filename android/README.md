# Android wrapper — merchant recovery prototype

This is a throwaway WebView wrapper that hosts the in-app prototype route
`/prototype/mobile?variant=A/B/C` and builds to an APK for on-device review.
It is the Android half of the wayfinder prototype (issue #19). The decision
(Variant B base + Variant C rail) lives in `app/api/prototype_mobile.py`;
the Android shell just renders it.

## What it does

- Loads the FastAPI prototype route in a WebView (no separate frontend runtime)
- Exposes the same 3 variants via the floating pill + `?variant=` URL (shareable)
- Bottom nav mirrors the 7-view contract: Overview, Recovery queue, RecoveryCase detail, PaymentExceptions, Policy settings, Investigation, Evaluation
- Keeps ClaimTags visible: ESTIMATED, SIMULATED, TEST MODE, MOCK

## Run without Android (quickest)

```sh
uv sync --dev
uv run uvicorn app.main:app --reload
# open http://127.0.0.1:8000/prototype/mobile?variant=B
```

That is the prototype. Switch variants with the pill or `?variant=A` / `B` / `C`.

## Build APK (Android SDK required)

Prereqs: Android SDK, JDK 17, and the FastAPI backend running where the
emulator/device can reach it. Emulator uses `10.0.2.2:8000` for host loopback.

```sh
# 1. Start backend
REROUTE_DATABASE_URL=sqlite:///./demo.db uv run uvicorn app.main:app --host 0.0.0.0 --port 8000

# 2. Configure the wrapper endpoint
# Edit android/app/src/main/java/com/reroute/merchant/MainActivity.kt
# WEBVIEW_URL = "http://10.0.2.2:8000/prototype/mobile?variant=B"  # emulator
# For physical device: use http://<your-host-ip>:8000/prototype/mobile?variant=B

# 3. Build
cd android
./gradlew assembleDebug
# APK at android/app/build/outputs/apk/debug/app-debug.apk

# 4. Install
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

No production credentials, no real money movement — the prototype shows MOCK
and TEST MODE separately per ADR 0006.

## Structure

```
android/
  settings.gradle
  build.gradle
  app/build.gradle
  app/src/main/AndroidManifest.xml
  app/src/main/java/com/reroute/merchant/MainActivity.kt
```

To point at a different host (e.g., staging), change `WEBVIEW_URL` and rebuild.
No secrets are embedded; provider Test Mode stays server-side.
