from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import desc, select

from app.db.tables import FindingAnalysis, LeakFinding
from app.domain.models import FindingAnalysisRequest
from app.finding_analysis import analysis_response, create_analysis
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


@router.post("/findings/{finding_id}/analysis", status_code=201)
def post_finding_analysis(
    finding_id: str,
    payload: FindingAnalysisRequest,
    request: Request,
    response: Response,
) -> dict:
    idempotency_key = payload.idempotency_key or request.headers.get("Idempotency-Key")
    if not idempotency_key:
        raise HTTPException(status_code=422, detail="idempotency_key is required")
    with request.app.state.session_factory() as session:
        finding = session.get(LeakFinding, finding_id)
        if finding is None:
            raise HTTPException(status_code=404, detail="finding not found")
        try:
            analysis, created = create_analysis(session, finding, idempotency_key)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        session.commit()
        response.status_code = 201 if created else 200
        return analysis_response(analysis)


@router.get("/findings/{finding_id}/analysis")
def get_finding_analysis(finding_id: str, request: Request) -> dict:
    with request.app.state.session_factory() as session:
        # source_finding_id is provenance, not a relationship: detector runs
        # replace LeakFinding rows and must not invalidate saved analyses.
        analysis = session.scalar(
            select(FindingAnalysis)
            .where(FindingAnalysis.source_finding_id == finding_id)
            .order_by(desc(FindingAnalysis.created_at), desc(FindingAnalysis.analysis_id))
        )
        if analysis is None:
            raise HTTPException(status_code=404, detail="finding analysis not found")
        return analysis_response(analysis)


@router.get("/finding-analyses/{analysis_id}")
def get_finding_analysis_by_id(analysis_id: str, request: Request) -> dict:
    with request.app.state.session_factory() as session:
        analysis = session.get(FindingAnalysis, analysis_id)
        if analysis is None:
            raise HTTPException(status_code=404, detail="finding analysis not found")
        return analysis_response(analysis)


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
