from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import func, select

from app.core.requests import read_limited_body
from app.db.tables import PaymentEvent
from app.ingestion.csv_loader import import_csv
from app.leak_analysis import detect_and_store_leaks
from simulator.generator import DEFAULT_SEED, HISTORICAL_PAYMENT_COUNT, generate_csv

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


@router.post("/simulate-999", status_code=status.HTTP_201_CREATED)
def simulate_999_payments(request: Request) -> dict[str, object]:
    """Create the deterministic Issue #47 merchant history.

    This endpoint intentionally refuses to mix the demo corpus into a database
    that already contains PaymentEvents. The live Razorpay Test Mode payment
    can therefore arrive afterwards as payment #1000 through the normal
    signed-webhook ingestion path.
    """
    csv_content = generate_csv(
        seed=DEFAULT_SEED,
        event_count=HISTORICAL_PAYMENT_COUNT,
    )

    with request.app.state.session_factory() as session:
        existing_payments = (
            session.scalar(select(func.count()).select_from(PaymentEvent)) or 0
        )
        if existing_payments:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="demo payment history requires an empty payment database",
            )

        imported, duplicates = import_csv(session, csv_content)
        if imported != HISTORICAL_PAYMENT_COUNT or duplicates:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="demo payment history could not be created cleanly",
            )

        findings = detect_and_store_leaks(session)
        session.commit()

        total = session.scalar(select(func.count()).select_from(PaymentEvent)) or 0
        failures = (
            session.scalar(
                select(func.count())
                .select_from(PaymentEvent)
                .where(PaymentEvent.status == "failed")
            )
            or 0
        )
        successes = (
            session.scalar(
                select(func.count())
                .select_from(PaymentEvent)
                .where(PaymentEvent.status == "captured")
            )
            or 0
        )

        return {
            "payments_created": imported,
            "payments_total": total,
            "successes": successes,
            "failures": failures,
            "duplicates": duplicates,
            "findings": len(findings),
            "top_finding_id": findings[0].finding_id if findings else None,
            "seed": DEFAULT_SEED,
        }
