from fastapi import APIRouter
from fastapi.responses import RedirectResponse

router = APIRouter(tags=["judge-compatibility"])


@router.get("/judge", response_class=RedirectResponse, include_in_schema=False)
def judge_page() -> RedirectResponse:
    return RedirectResponse(url="/", status_code=307)
