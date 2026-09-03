from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.api.incidents import router as incidents_router

router = APIRouter(tags=["judge-demo"])
router.include_router(incidents_router)
JUDGE_HTML = (
    Path(__file__).resolve().parent.parent / "templates" / "judge.html"
).read_text(encoding="utf-8")


@router.get("/judge", response_class=HTMLResponse, include_in_schema=False)
def judge_page() -> str:
    return JUDGE_HTML
