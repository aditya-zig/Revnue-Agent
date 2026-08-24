from fastapi import APIRouter, Request

from app.leak_analysis import detect_leaks

router = APIRouter(prefix="/api/v1", tags=["leak findings"])


@router.get("/findings")
def list_findings(request: Request) -> list[dict]:
    with request.app.state.session_factory() as session:
        findings = detect_leaks(session)
        session.commit()
        return [
            {
                "finding_id": finding.finding_id,
                "detector_version": finding.detector_version,
                "cohort_filter": finding.cohort_filter,
                "baseline_rate": finding.baseline_rate,
                "observed_rate": finding.observed_rate,
                "impact": finding.impact,
                "recoverable_impact": finding.recoverable_impact,
                "confidence": finding.confidence,
                "evidence": finding.evidence_json,
            }
            for finding in findings
        ]
