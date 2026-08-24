from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import FastAPI

from app.api.cases import router as cases_router
from app.api.data import router as data_router
from app.api.evaluations import router as evaluations_router
from app.api.leak_findings import router as leak_findings_router
from app.api.webhooks import router as webhooks_router
from app.core.config import Settings
from app.db.session import create_session_factory


def _payment_link_not_configured(amount: int, idempotency_key: str) -> str:
    raise RuntimeError("payment link provider is not configured")


def create_app(
    database_url: str | None = None,
    webhook_secret: str | None = None,
    max_request_body_bytes: int | None = None,
    policy_now: Callable[[], datetime] | None = None,
    create_payment_link: Callable[[int, str], str] | None = None,
) -> FastAPI:
    settings = Settings()
    app = FastAPI(title="ReRoute Intelligence")
    app.state.session_factory = create_session_factory(database_url or settings.database_url)
    app.state.webhook_secret = (
        webhook_secret if webhook_secret is not None else settings.razorpay_webhook_secret
    )
    app.state.max_request_body_bytes = (
        max_request_body_bytes
        if max_request_body_bytes is not None
        else settings.max_request_body_bytes
    )
    app.state.policy_now = policy_now or (lambda: datetime.now(UTC))
    app.state.quiet_hours_start = settings.quiet_hours_start
    app.state.quiet_hours_end = settings.quiet_hours_end
    app.state.create_payment_link = create_payment_link or _payment_link_not_configured
    app.include_router(webhooks_router)
    app.include_router(cases_router)
    app.include_router(data_router)
    app.include_router(evaluations_router)
    app.include_router(leak_findings_router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
