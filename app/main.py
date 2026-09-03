import os
from collections.abc import Callable
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, text

from app.core.config import Settings
from app.db.session import create_session_factory, normalize_database_url
from app.finding_analysis import FindingAnalysisProvider, OpenRouterProvider
from app.recovery import RecoveryModel

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
REQUIRED_TABLES = {
    "checkout_orders",
    "leak_findings",
    "payment_events",
    "recovery_cases",
}
ROUTER_MODULES = (
    ("webhooks", "app.api.webhooks"),
    ("cases", "app.api.cases"),
    ("data", "app.api.data"),
    ("evaluations", "app.api.evaluations"),
    ("leak_findings", "app.api.leak_findings"),
    ("payment_exceptions", "app.api.payment_exceptions"),
    ("operator_controls", "app.api.operator_controls"),
    ("storefront", "app.api.storefront"),
    ("judge", "app.api.judge"),
    ("dashboard", "app.api.dashboard"),
)
DATABASE_ENV_CANDIDATES = (
    "REROUTE_DATABASE_URL",
    "DATABASE_URL",
    "POSTGRES_URL",
    "POSTGRES_PRISMA_URL",
    "POSTGRES_URL_NON_POOLING",
)


def _payment_link_not_configured(amount: int, idempotency_key: str) -> str:
    raise RuntimeError("payment link provider is not configured")


def _order_not_configured(amount: int, idempotency_key: str) -> str:
    raise RuntimeError("Razorpay Test Mode order provider is not configured")


def _database_not_configured():
    raise RuntimeError("database is not configured")


def _supported_database_url(value: str | None) -> bool:
    if not value:
        return False
    normalized = normalize_database_url(value)
    return normalized.startswith(("postgresql://", "sqlite"))


def _select_database_url(settings: Settings, explicit: str | None) -> tuple[str, str]:
    if explicit is not None:
        return explicit, "explicit"
    for env_name in DATABASE_ENV_CANDIDATES:
        value = os.getenv(env_name)
        if _supported_database_url(value):
            return value or "", env_name.lower()
    return settings.database_url, "settings"


def _build_razorpay_creator_from_settings(
    settings: Settings, key_id: str | None = None, key_secret: str | None = None
):
    effective_key_id = settings.razorpay_key_id if key_id is None else key_id
    effective_key_secret = settings.razorpay_key_secret if key_secret is None else key_secret
    if effective_key_id.startswith("rzp_test_") and effective_key_secret:
        from app.integrations.razorpay import build_payment_link_creator

        return build_payment_link_creator(effective_key_id, effective_key_secret)
    return None


def _build_razorpay_order_creator_from_settings(
    settings: Settings, key_id: str | None = None, key_secret: str | None = None
):
    effective_key_id = settings.razorpay_key_id if key_id is None else key_id
    effective_key_secret = settings.razorpay_key_secret if key_secret is None else key_secret
    if effective_key_id.startswith("rzp_test_") and effective_key_secret:
        from app.integrations.razorpay import build_order_creator

        return build_order_creator(effective_key_id, effective_key_secret)
    return None


def _include_routers(app: FastAPI) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for name, module_name in ROUTER_MODULES:
        try:
            module = import_module(module_name)
            app.include_router(module.router)
        except Exception:
            statuses[name] = "load_error"
        else:
            statuses[name] = "ok"
    return statuses


def _database_readiness(app: FastAPI) -> str:
    if app.state.database_configuration != "ok":
        return app.state.database_configuration
    try:
        with app.state.session_factory() as session:
            session.execute(text("SELECT 1"))
            bind = session.get_bind()
            table_names = set(inspect(bind).get_table_names())
    except Exception:
        return "unreachable"
    if not REQUIRED_TABLES.issubset(table_names):
        return "schema_missing"
    return "ready"


def create_app(
    database_url: str | None = None,
    webhook_secret: str | None = None,
    max_request_body_bytes: int | None = None,
    policy_now: Callable[[], datetime] | None = None,
    create_payment_link: Callable[[int, str], str] | None = None,
    create_order: Callable[[int, str], str | dict] | None = None,
    find_order_by_receipt: Callable[..., dict | str | None] | None = None,
    razorpay_key_id: str | None = None,
    razorpay_key_secret: str | None = None,
    decide_recovery_action: Callable[[dict], object] | None = None,
    kill_switch: bool | None = None,
    finding_analysis_provider: FindingAnalysisProvider | None = None,
    openrouter_api_key: str | None = None,
) -> FastAPI:
    app = FastAPI(title="ReRoute Intelligence")

    try:
        settings = Settings()
    except Exception:
        settings = Settings.model_construct()
        app.state.settings_configuration = "configuration_error"
    else:
        app.state.settings_configuration = "ok"

    try:
        app.mount(
            "/static",
            StaticFiles(directory=STATIC_DIR, check_dir=False),
            name="static",
        )
    except Exception:
        app.state.static_configuration = "load_error"
    else:
        app.state.static_configuration = "ok"

    effective_database_url, database_source = _select_database_url(settings, database_url)
    app.state.database_source = database_source
    try:
        app.state.session_factory = create_session_factory(effective_database_url)
    except Exception:
        app.state.session_factory = _database_not_configured
        app.state.database_configuration = "configuration_error"
        app.state.database_configuration_reason = (
            "unsupported_scheme"
            if not _supported_database_url(effective_database_url)
            else "invalid_url"
        )
    else:
        app.state.database_configuration = "ok"
        app.state.database_configuration_reason = "ok"

    app.state.webhook_secret = (
        webhook_secret if webhook_secret is not None else settings.razorpay_webhook_secret
    )
    app.state.settings = settings
    app.state.razorpay_key_id = (
        razorpay_key_id if razorpay_key_id is not None else settings.razorpay_key_id
    )
    app.state.razorpay_key_secret = (
        razorpay_key_secret if razorpay_key_secret is not None else settings.razorpay_key_secret
    )
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

    razorpay_creator = _build_razorpay_creator_from_settings(
        settings, razorpay_key_id, razorpay_key_secret
    )
    app.state.create_payment_link = (
        create_payment_link or razorpay_creator or _payment_link_not_configured
    )
    razorpay_order_creator = _build_razorpay_order_creator_from_settings(
        settings, razorpay_key_id, razorpay_key_secret
    )
    app.state.create_order = create_order or razorpay_order_creator or _order_not_configured
    app.state.find_order_by_receipt = find_order_by_receipt or getattr(
        create_order, "reconcile", None
    ) or (razorpay_order_creator.reconcile if razorpay_order_creator is not None else None)
    app.state.checkout_key_id = (
        app.state.razorpay_key_id
        if app.state.razorpay_key_id.startswith("rzp_test_")
        else "rzp_test_local"
        if create_order is not None and not app.state.razorpay_key_id
        else ""
    )
    app.state.checkout_key_secret = app.state.razorpay_key_secret
    app.state.decide_recovery_action = decide_recovery_action
    app.state.finding_analysis_provider = finding_analysis_provider or OpenRouterProvider(
        api_key=settings.openrouter_api_key if openrouter_api_key is None else openrouter_api_key,
        timeout=settings.openrouter_timeout_seconds,
        http_referer=settings.openrouter_http_referer or None,
    )
    app.state.recovery_model = RecoveryModel()
    app.state.router_statuses = _include_routers(app)

    @app.get("/health")
    def health() -> dict[str, object]:
        database = _database_readiness(app)
        routers_ready = all(status == "ok" for status in app.state.router_statuses.values())
        ready = (
            app.state.settings_configuration == "ok"
            and app.state.static_configuration == "ok"
            and routers_ready
            and database == "ready"
        )
        return {
            "status": "ok",
            "ready": ready,
            "components": {
                "settings": app.state.settings_configuration,
                "static": app.state.static_configuration,
                "database": database,
                "database_source": app.state.database_source,
                "database_configuration_reason": app.state.database_configuration_reason,
                "routers": app.state.router_statuses,
            },
        }

    return app


app = create_app()
