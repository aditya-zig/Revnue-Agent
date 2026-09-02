from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from uuid import uuid4


def request_json(url: str, *, method: str = "GET", body: dict | None = None) -> object:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"app_http_status={error.code}") from error
    except Exception as error:
        raise RuntimeError(f"app_request={type(error).__name__}") from error


def summarize_analysis(analysis: dict) -> dict[str, object]:
    metadata = analysis.get("provider_metadata") or {}
    result = analysis.get("result") or {}
    external = result.get("external_model_generated") is True
    fallback = metadata.get("fallback_used") is True
    return {
        "ready": bool(metadata.get("provider") == "openrouter" and external and not fallback),
        "provider": metadata.get("provider"),
        "requested_model": metadata.get("requested_model"),
        "resolved_model": metadata.get("resolved_model"),
        "external_model_generated": external,
        "fallback_used": fallback,
        "failure_reason": metadata.get("failure_reason"),
        "hypothesis_count": len(result.get("hypotheses") or []),
        "validation_step_count": len(result.get("next_validation_steps") or []),
    }


def verify(base_url: str) -> dict[str, object]:
    base = base_url.rstrip("/")
    findings = request_json(f"{base}/api/v1/findings")
    if not isinstance(findings, list) or not findings:
        raise RuntimeError("no_findings_available")
    finding = findings[0]
    if not isinstance(finding, dict) or not isinstance(finding.get("finding_id"), str):
        raise RuntimeError("finding_response_invalid")
    analysis = request_json(
        f"{base}/api/v1/findings/{finding['finding_id']}/analysis",
        method="POST",
        body={"idempotency_key": f"openrouter-smoke-{uuid4().hex}"},
    )
    if not isinstance(analysis, dict):
        raise RuntimeError("analysis_response_invalid")
    return summarize_analysis(analysis)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify ReRoute's bounded OpenRouter analysis path.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    try:
        report = verify(args.base_url)
    except RuntimeError as error:
        print(json.dumps({"ready": False, "error": str(error)}, indent=2))
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ready"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
