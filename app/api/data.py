from fastapi import APIRouter, HTTPException, Request, status

from app.core.requests import read_limited_body
from app.ingestion.csv_loader import import_csv

router = APIRouter(prefix="/api/v1/data", tags=["data"])


@router.post("/import", status_code=status.HTTP_201_CREATED)
async def import_data(request: Request) -> dict[str, int]:
    try:
        content = (await read_limited_body(request)).decode("utf-8")
    except UnicodeDecodeError as error:
        raise HTTPException(status_code=422, detail="CSV must be UTF-8") from error

    with request.app.state.session_factory() as session:
        try:
            imported, duplicates = import_csv(session, content)
        except ValueError as error:
            session.rollback()
            raise HTTPException(status_code=422, detail=str(error)) from error
        session.commit()
    return {"imported": imported, "duplicates": duplicates}
