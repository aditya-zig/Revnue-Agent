from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.db.tables import LeakFinding
from app.leak_analysis import detect_and_store_leaks, finding_sort_key

router = APIRouter(prefix="/api/v1", tags=["leak findings"])


@router.get("/findings")
def list_findings(request: Request) -> list[dict]:
    with request.app.state.session_factory() as session:
        findings = session.scalars(select(LeakFinding)).all()
        findings.sort(key=finding_sort_key)
        return [_finding_response(finding) for finding in findings]


@router.post("/findings/detect")
def detect_findings(request: Request) -> list[dict]:
    with request.app.state.session_factory() as session:
        findings = detect_and_store_leaks(session)
        session.commit()
        return [_finding_response(finding) for finding in findings]


@router.get("/findings/{finding_id}")
def get_finding(finding_id: str, request: Request) -> dict:
    with request.app.state.session_factory() as session:
        finding = session.scalar(
            select(LeakFinding).where(LeakFinding.finding_id == finding_id)
        )
        if finding is None:
            raise HTTPException(status_code=404, detail="finding not found")
        return _finding_response(finding)


def _finding_response(finding: LeakFinding) -> dict:
    return {
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
