from fastapi import APIRouter, Query

from app.evaluation import run_baseline

router = APIRouter(prefix="/api/v1/evaluations", tags=["evaluations"])


@router.post("/baseline")
def evaluate_baseline(seed: int = Query(ge=0)) -> dict[str, int | float]:
    return run_baseline(seed)
