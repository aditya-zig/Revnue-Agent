"""Deterministic, advisory analyses of aggregate LeakFindings.

This module intentionally has no provider or model integration.  It creates the
small aggregate snapshot that a future analyzer may consume and produces the
local fallback used until that integration is separately delivered.
"""

import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.tables import FindingAnalysis, LeakFinding
from app.domain.enums import ClaimTag

DETERMINISTIC_ANALYSIS_VERSION = "deterministic-finding-analysis-v1"


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
    hypothesis = (
        "This cohort may represent a repeatable payment failure pattern; validate it "
        "with fresh PaymentEvents before changing Policy."
    )
    return {
        "analysis_version": DETERMINISTIC_ANALYSIS_VERSION,
        "summary": (
            f"Observed {snapshot['failure_count']} failures in the {dimension} cohort "
            f"{value}; this is an advisory estimate, not a recovery outcome."
        ),
        "observed_facts": facts,
        "hypotheses": [hypothesis],
        "next_validation_steps": [
            "Compare this cohort with a later detector run.",
            "Review the underlying normalized PaymentEvents before changing Policy.",
        ],
        "external_model_generated": False,
        "model_statement": "No external model generated this analysis.",
        "claim_tag": snapshot["claim_tag"],
    }


def analysis_response(analysis: FindingAnalysis) -> dict:
    created_at = analysis.created_at
    if created_at is not None and created_at.tzinfo is None:
        # SQLite drops timezone metadata when a saved record is reloaded.
        created_at = created_at.replace(tzinfo=UTC)
    return {
        "analysis_id": analysis.analysis_id,
        "finding_id": analysis.source_finding_id,
        "snapshot_hash": analysis.snapshot_hash,
        "idempotency_key": analysis.idempotency_key,
        "snapshot": analysis.snapshot_json,
        "input_snapshot": analysis.snapshot_json,
        "result": analysis.result_json,
        "output": analysis.result_json,
        "impact_paise": analysis.impact_paise,
        "recoverable_impact_paise": analysis.recoverable_impact_paise,
        "claim_tag": analysis.claim_tag,
        "created_at": created_at.isoformat() if created_at else None,
    }


def create_analysis(
    session: Session, finding: LeakFinding, idempotency_key: str
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

    created_at = datetime.now(UTC)
    analysis = FindingAnalysis(
        # Timestamp prefix makes the latest-record projection deterministic even
        # on SQLite, which has no monotonic timestamp ordering guarantee.
        analysis_id=f"analysis_{created_at.strftime('%Y%m%d%H%M%S%f')}_{uuid4().hex}",
        source_finding_id=finding.finding_id,
        snapshot_hash=digest,
        idempotency_key=idempotency_key,
        snapshot_json=snapshot,
        result_json=deterministic_result(snapshot),
        impact_paise=finding.impact,
        recoverable_impact_paise=finding.recoverable_impact,
        claim_tag=ClaimTag.ESTIMATED.value,
        created_at=created_at,
    )
    session.add(analysis)
    try:
        session.flush()
    except IntegrityError:
        # Make retries safe if two explicit requests race on the unique key.
        session.rollback()
        existing = session.scalar(
            select(FindingAnalysis).where(FindingAnalysis.idempotency_key == idempotency_key)
        )
        if existing is None or existing.snapshot_hash != digest:
            raise
        return existing, False
    return analysis, True
