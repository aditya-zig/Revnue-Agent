"""Advisory FindingAnalysis with a constrained OpenRouter boundary.

The application owns the sanitized snapshot and observed facts. OpenRouter may
only provide hypotheses and validation steps in a strict, bounded response;
any provider or parsing failure is persisted as the deterministic fallback.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.tables import FindingAnalysis, LeakFinding
from app.domain.enums import ClaimTag

DETERMINISTIC_ANALYSIS_VERSION = "deterministic-finding-analysis-v1"
OPENROUTER_PROVIDER = "openrouter"
OPENROUTER_MODEL = "openrouter/free"
OPENROUTER_PROMPT_VERSION = "openrouter-finding-analysis-v1"
OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterProviderError(RuntimeError):
    """A provider-boundary failure safe to turn into a deterministic fallback."""

    def __init__(self, reason: str, status_code: int | None = None):
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


@dataclass(frozen=True)
class OpenRouterCompletion:
    output: str
    resolved_model: str | None = None
    generation_id: str | None = None
    usage: dict | None = None
    tool_usage: dict | None = None


class FindingAnalysisProvider(Protocol):
    requested_model: str

    def generate(self, snapshot: dict) -> OpenRouterCompletion:
        """Generate only the model-authored portion of a finding analysis."""


class FindingAnalysisOutput(BaseModel):
    """The only fields an external model is allowed to author."""

    model_config = ConfigDict(extra="forbid")

    hypotheses: list[str] = Field(min_length=1, max_length=5)
    next_validation_steps: list[str] = Field(min_length=1, max_length=5)


class OpenRouterProvider:
    """Small synchronous OpenRouter chat-completions adapter.

    This adapter deliberately has no fallback model and does not expose tools.
    """

    def __init__(
        self,
        api_key: str | None,
        requested_model: str = OPENROUTER_MODEL,
        endpoint: str = OPENROUTER_ENDPOINT,
        timeout: float = 10.0,
        http_referer: str | None = None,
    ):
        self.api_key = api_key or ""
        self.requested_model = requested_model
        self.endpoint = endpoint
        self.timeout = timeout
        self.http_referer = http_referer

    def generate(self, snapshot: dict) -> OpenRouterCompletion:
        if not self.api_key:
            raise OpenRouterProviderError("missing_credentials")

        payload = {
            "model": self.requested_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You analyze a payment-failure cohort. Use only the supplied sanitized "
                        "aggregate snapshot. Return concise hypotheses and next validation "
                        "steps. Do not state observed facts, take actions, browse, or use tools."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Produce the strict JSON schema response for this snapshot:\n"
                        f"{_json_for_prompt(snapshot)}"
                    ),
                },
            ],
            "max_tokens": 400,
            "provider": {"allow_fallbacks": False, "data_collection": "deny"},
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "finding_analysis",
                    "strict": True,
                    "schema": FindingAnalysisOutput.model_json_schema(),
                },
            },
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        if self.http_referer:
            headers["HTTP-Referer"] = self.http_referer

        try:
            response = httpx.post(
                self.endpoint,
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
        except httpx.TimeoutException as error:
            raise OpenRouterProviderError("timeout") from error
        except httpx.RequestError as error:
            raise OpenRouterProviderError("connection_error") from error

        if response.status_code >= 400:
            reason = "rate_limited" if response.status_code == 429 else "provider_error"
            raise OpenRouterProviderError(reason, status_code=response.status_code)

        try:
            body = response.json()
        except ValueError as error:
            raise OpenRouterProviderError("malformed_provider_response") from error
        try:
            choice = body["choices"][0]
            message = choice["message"]
            content = message["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise OpenRouterProviderError("malformed_provider_response") from error
        tool_calls = message.get("tool_calls") or []
        tool_usage = {"requested": False, "used": bool(tool_calls), "tools": []}
        if tool_calls:
            tool_usage["tools"] = [call.get("type", "unknown") for call in tool_calls]
        if not isinstance(content, str):
            raise OpenRouterProviderError("malformed_provider_response")
        return OpenRouterCompletion(
            output=content,
            resolved_model=body.get("model"),
            generation_id=body.get("id"),
            usage=body.get("usage") if isinstance(body.get("usage"), dict) else None,
            tool_usage=tool_usage,
        )


def _json_for_prompt(value: dict) -> str:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def create_snapshot(finding: LeakFinding) -> dict:
    """Return only normalized aggregate values safe for analysis storage."""
    evidence = finding.evidence_json or {}
    return {
        "analysis_version": DETERMINISTIC_ANALYSIS_VERSION,
        "detector_version": finding.detector_version,
        "cohort_filter": {
            "dimension": finding.cohort_filter.get("dimension"),
            "value": finding.cohort_filter.get("value"),
        },
        "baseline_rate": finding.baseline_rate,
        "observed_rate": finding.observed_rate,
        "confidence": finding.confidence,
        "support": evidence.get("support", 0),
        "failure_count": evidence.get("failure_count", 0),
        "attempted_value_paise": evidence.get("attempted_value", 0),
        "failed_value_paise": evidence.get("failed_value", 0),
        "unresolved_value_paise": evidence.get("unresolved_value", 0),
        "impact_paise": finding.impact,
        "recoverable_impact_paise": finding.recoverable_impact,
        "recovery_probability": evidence.get("recovery_probability", 0),
        "data_quality_warnings": list(evidence.get("data_quality_warnings", [])),
        "claim_tag": ClaimTag.ESTIMATED.value,
        "money_claim_tags": {
            "attempted_value_paise": ClaimTag.ESTIMATED.value,
            "failed_value_paise": ClaimTag.ESTIMATED.value,
            "unresolved_value_paise": ClaimTag.ESTIMATED.value,
            "impact_paise": ClaimTag.ESTIMATED.value,
            "recoverable_impact_paise": ClaimTag.ESTIMATED.value,
        },
    }


def snapshot_hash(snapshot: dict) -> str:
    import hashlib
    import json

    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def deterministic_result(snapshot: dict) -> dict:
    cohort = snapshot["cohort_filter"]
    dimension = cohort["dimension"]
    value = cohort["value"]
    facts = [
        {"label": "Cohort", "value": f"{dimension} = {value}"},
        {"label": "Cohort support", "value": snapshot["support"]},
        {"label": "Observed failure rate", "value": snapshot["observed_rate"]},
        {"label": "Baseline failure rate", "value": snapshot["baseline_rate"]},
        {
            "label": "Impact",
            "value_paise": snapshot["impact_paise"],
            "claim_tag": snapshot["claim_tag"],
        },
        {
            "label": "Estimated recoverable impact",
            "value_paise": snapshot["recoverable_impact_paise"],
            "claim_tag": snapshot["claim_tag"],
        },
    ]
    return {
        "analysis_version": DETERMINISTIC_ANALYSIS_VERSION,
        "summary": (
            f"Observed {snapshot['failure_count']} failures in the {dimension} cohort "
            f"{value}; this is an advisory estimate, not a recovery outcome."
        ),
        "observed_facts": facts,
        "hypotheses": [
            "This cohort may represent a repeatable payment failure pattern; validate it "
            "with fresh PaymentEvents before changing Policy."
        ],
        "next_validation_steps": [
            "Compare this cohort with a later detector run.",
            "Review the underlying normalized PaymentEvents before changing Policy.",
        ],
        "external_model_generated": False,
        "model_statement": "No external model generated this analysis.",
        "claim_tag": snapshot["claim_tag"],
    }


def _metadata(
    provider: FindingAnalysisProvider,
    completion: OpenRouterCompletion | None = None,
    failure_reason: str | None = None,
) -> dict:
    return {
        "provider": OPENROUTER_PROVIDER,
        "requested_model": provider.requested_model,
        "resolved_model": completion.resolved_model if completion else None,
        "provider_generation_id": completion.generation_id if completion else None,
        "prompt_version": OPENROUTER_PROMPT_VERSION,
        "usage": completion.usage if completion else None,
        "tool_usage": (
            completion.tool_usage
            if completion and completion.tool_usage is not None
            else {"requested": False, "used": False, "tools": []}
        ),
        "failure_reason": failure_reason,
        "fallback_used": failure_reason is not None,
    }


def _failure_reason(error: Exception) -> str:
    if isinstance(error, OpenRouterProviderError):
        return error.reason if error.status_code is None else f"{error.reason}_{error.status_code}"
    if isinstance(error, (ValueError, ValidationError)):
        return "malformed_output"
    return "provider_error"


def generate_result(
    snapshot: dict, provider: FindingAnalysisProvider
) -> tuple[dict, dict]:
    """Return a model result or the deterministic fallback and its audit metadata."""
    try:
        completion = provider.generate(snapshot)
        if completion.tool_usage and completion.tool_usage.get("used"):
            raise OpenRouterProviderError("unexpected_tool_use")
        parsed = FindingAnalysisOutput.model_validate_json(completion.output)
    except Exception as error:  # Provider boundary must never affect the request path.
        return deterministic_result(snapshot), _metadata(
            provider, failure_reason=_failure_reason(error)
        )

    result = deterministic_result(snapshot)
    result.update(parsed.model_dump())
    result["external_model_generated"] = True
    result["model_statement"] = "Hypotheses and validation steps generated by an OpenRouter model."
    return result, _metadata(provider, completion=completion)


def analysis_response(analysis: FindingAnalysis) -> dict:
    created_at = analysis.created_at
    if created_at is not None and created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    metadata = {
        "provider": analysis.provider,
        "requested_model": analysis.requested_model,
        "resolved_model": analysis.resolved_model,
        "provider_generation_id": analysis.provider_generation_id,
        "prompt_version": analysis.prompt_version,
        "usage": analysis.usage_json,
        "tool_usage": analysis.tool_usage_json,
        "failure_reason": analysis.failure_reason,
        "fallback_used": analysis.fallback_used,
    }
    return {
        "analysis_id": analysis.analysis_id,
        "finding_id": analysis.source_finding_id,
        "snapshot_hash": analysis.snapshot_hash,
        "idempotency_key": analysis.idempotency_key,
        "snapshot": analysis.snapshot_json,
        "input_snapshot": analysis.snapshot_json,
        "result": analysis.result_json,
        "output": analysis.result_json,
        "provider_metadata": metadata,
        "impact_paise": analysis.impact_paise,
        "recoverable_impact_paise": analysis.recoverable_impact_paise,
        "claim_tag": analysis.claim_tag,
        "created_at": created_at.isoformat() if created_at else None,
    }


def create_analysis(
    session: Session,
    finding: LeakFinding,
    idempotency_key: str,
    provider: FindingAnalysisProvider | None = None,
) -> tuple[FindingAnalysis, bool]:
    snapshot = create_snapshot(finding)
    digest = snapshot_hash(snapshot)
    existing = session.scalar(
        select(FindingAnalysis).where(FindingAnalysis.idempotency_key == idempotency_key)
    )
    if existing is not None:
        if existing.snapshot_hash != digest:
            raise ValueError("idempotency key was already used for another finding snapshot")
        return existing, False

    provider = provider or OpenRouterProvider(api_key=None)
    result, metadata = generate_result(snapshot, provider)
    created_at = datetime.now(UTC)
    analysis = FindingAnalysis(
        analysis_id=f"analysis_{created_at.strftime('%Y%m%d%H%M%S%f')}_{uuid4().hex}",
        source_finding_id=finding.finding_id,
        snapshot_hash=digest,
        idempotency_key=idempotency_key,
        snapshot_json=snapshot,
        result_json=result,
        provider=metadata["provider"],
        requested_model=metadata["requested_model"],
        resolved_model=metadata["resolved_model"],
        provider_generation_id=metadata["provider_generation_id"],
        prompt_version=metadata["prompt_version"],
        usage_json=metadata["usage"],
        tool_usage_json=metadata["tool_usage"],
        failure_reason=metadata["failure_reason"],
        fallback_used=metadata["fallback_used"],
        impact_paise=finding.impact,
        recoverable_impact_paise=finding.recoverable_impact,
        claim_tag=ClaimTag.ESTIMATED.value,
        created_at=created_at,
    )
    session.add(analysis)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(
            select(FindingAnalysis).where(FindingAnalysis.idempotency_key == idempotency_key)
        )
        if existing is None or existing.snapshot_hash != digest:
            raise
        return existing, False
    return analysis, True
