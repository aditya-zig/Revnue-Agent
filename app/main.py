from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.cases import router as cases_router
from app.api.dashboard import router as dashboard_router
from app.api.data import router as data_router
from app.api.evaluations import router as evaluations_router
from app.api.leak_findings import router as leak_findings_router
from app.api.operator_controls import router as operator_controls_router
from app.api.payment_exceptions import router as payment_exceptions_router
from app.api.webhooks import router as webhooks_router
from app.core.config import Settings
from app.db.session import create_session_factory
from app.finding_analysis import FindingAnalysisProvider, OpenRouterProvider
from app.recovery import RecoveryModel


def _payment_link_not_configured(amount: int, idempotency_key: str) -> str:
    raise RuntimeError("payment link provider is not configured")


def _build_razorpay_creator_from_settings(settings: Settings):
    if settings.razorpay_key_id and settings.razorpay_key_secret:
        from app.integrations.razorpay import build_payment_link_creator

        return build_payment_link_creator(settings.razorpay_key_id, settings.razorpay_key_secret)
    return None


def create_app(
    database_url: str | None = None,
    webhook_secret: str | None = None,
    max_request_body_bytes: int | None = None,
    policy_now: Callable[[], datetime] | None = None,
    create_payment_link: Callable[[int, str], str] | None = None,
    decide_recovery_action: Callable[[dict], object] | None = None,
    kill_switch: bool | None = None,
    finding_analysis_provider: FindingAnalysisProvider | None = None,
    openrouter_api_key: str | None = None,
) -> FastAPI:
    settings = Settings()
    app = FastAPI(title="ReRoute Intelligence")
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    app.state.session_factory = create_session_factory(database_url or settings.database_url)
    app.state.webhook_secret = (
        webhook_secret if webhook_secret is not None else settings.razorpay_webhook_secret
    )
    app.state.settings = settings
    app.state.max_request_body_bytes = (
        max_request_body_bytes
        if max_request_body_bytes is not None
        else settings.max_request_body_bytes
    )
    app.state.policy_now = policy_now or (lambda: datetime.now(UTC))
    app.state.quiet_hours_start = settings.quiet_hours_start
    app.state.quiet_hours_end = settings.quiet_hours_end
    app.state.contact_limit = 3
    app.state.mock_identity = "ReRoute demo"
    app.state.kill_switch = kill_switch if kill_switch is not None else settings.kill_switch
    # Prefer injected creator, then real Razorpay if keys are set, else mock failure
    razorpay_creator = _build_razorpay_creator_from_settings(settings)
    app.state.create_payment_link = (
        create_payment_link or razorpay_creator or _payment_link_not_configured
    )
    app.state.decide_recovery_action = decide_recovery_action
    app.state.finding_analysis_provider = finding_analysis_provider or OpenRouterProvider(
        api_key=settings.openrouter_api_key if openrouter_api_key is None else openrouter_api_key,
        timeout=settings.openrouter_timeout_seconds,
        http_referer=settings.openrouter_http_referer or None,
    )
    app.state.recovery_model = RecoveryModel()
    app.include_router(webhooks_router)
    app.include_router(cases_router)
    app.include_router(data_router)
    app.include_router(evaluations_router)
    app.include_router(leak_findings_router)
    app.include_router(payment_exceptions_router)
    app.include_router(operator_controls_router)
    app.include_router(dashboard_router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
