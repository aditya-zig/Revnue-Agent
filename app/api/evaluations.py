import json
from pathlib import Path

from fastapi import APIRouter, Query, Request

from app.evaluation import run_baseline, run_comparison

router = APIRouter(prefix="/api/v1/evaluations", tags=["evaluations"])


@router.post("/baseline")
def evaluate_baseline(seed: int = Query(ge=0)) -> dict[str, int | float]:
    return run_baseline(seed)


@router.get("/published")
def get_published_evaluation() -> dict:
    directory = Path(__file__).parent.parent / "evaluation"
    return {
        "results": json.loads((directory / "published_results.json").read_text()),
        "exceptions": json.loads((directory / "published_exceptions.json").read_text()),
    }


@router.get("/reproducible")
def get_reproducible_evaluation() -> dict:
    return run_comparison()


@router.get("/recovery-model")
def get_recovery_model_report(request: Request) -> dict:
    return request.app.state.recovery_model.report
