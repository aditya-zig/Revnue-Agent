import os
from collections.abc import Callable
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, text
from sqlalchemy.engine import URL

from app.core.config import Settings
from app.db.session import create_session_factory, normalize_database_url
from app.db.tables import Base
from app.finding_analysis import FindingAnalysisProvider, OpenRouterProvider
from app.recovery import RecoveryModel

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
VERCEL_FALLBACK_DATABASE_URL = "sqlite:////tmp/reroute-demo.db"
REQUIRED_TABLES = {
    "checkout_orders",
    "leak_findings",
    "payment_events",
    "recovery_cases",
}
ROUTER_MODULES = (
    ("webhooks", "app.api.webhooks"),
    ("cases", "app.api.cases"),
    ("incidents", "app.api.incidents"),
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
    "SUPABASE_DB_URL",
    "SUPABASE_DATABASE_URL",
    "DIRECT_URL",
)
PASSWORD_PLACEHOLDERS = (
    "[YOUR-PASSWORD]",
    "[YOUR_PASSWORD]",
    "<YOUR-PASSWORD>",
    "<YOUR_PASSWORD>",
    "YOUR-PASSWORD",
    "YOUR_PASSWORD",
)


def _payment_link_not_configured(amount: int, idempotency_key: str) -> str:
    raise RuntimeError("payment link provider is not configured")


def _order_not_configured(amount: int, idempotency_key: str) -> str:
    raise RuntimeError("Razorpay Test Mode order provider is not configured")


def _database_not_configured():
    raise RuntimeError("database is not configured")


def _supported_database_url(value: str | URL | None) -> bool:
    if value is None:
        return False
    if isinstance(value, URL):
        return value.get_backend_name() in {"postgresql", "sqlite"}
    if not value:
        return False
    normalized = normalize_database_url(value)
    return normalized.startswith(("postgresql://", "sqlite"))


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def _database_url_from_components() -> tuple[URL, str] | None:
    groups = (
        (
            "postgres_components",
            _first_env("POSTGRES_HOST"),
            _first_env("POSTGRES_USER"),
            _first_env("POSTGRES_PASSWORD"),
            _first_env("POSTGRES_DATABASE", "POSTGRES_DB"),
            _first_env("POSTGRES_PORT"),
        ),
        (
            "pg_components",
            _first_env("PGHOST"),
            _first_env("PGUSER"),
            _first_env("PGPASSWORD"),
            _first_env("PGDATABASE"),
            _first_env("PGPORT"),
        ),
        (
            "supabase_components",
            _first_env("SUPABASE_DB_HOST"),
            _first_env("SUPABASE_DB_USER"),
            _first_env("SUPABASE_DB_PASSWORD"),
            _first_env("SUPABASE_DB_NAME", "SUPABASE_DB_DATABASE"),
            _first_env("SUPABASE_DB_PORT"),
        ),
    )
    for source, host, user, password, database, port_value in groups:
        if not all((host, user, password, database)):
            continue
        try:
            port = int(port_value) if port_value else 5432
        except ValueError:
            continue
        return (
            URL.create(
                "postgresql+psycopg2",
                username=user,
                password=password,
                host=host,
                port=port,
                database=database,
            ),
            source,
        )
    return None


def _database_env_capabilities() -> dict[str, bool]:
    return {
        "supported_full_url": any(
            _supported_database_url(os.getenv(env_name))
            for env_name in DATABASE_ENV_CANDIDATES
        ),
        "structured_postgres": _database_url_from_components() is not None,
        "supabase_http_url": bool(_first_env("SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_URL")),
        "supabase_key": bool(
            _first_env(
                "SUPABASE_SERVICE_ROLE_KEY",
                "SUPABASE_ANON_KEY",
                "NEXT_PUBLIC_SUPABASE_ANON_KEY",
            )
        ),
    }


def _select_database_url(settings: Settings, explicit: str | None) -> tuple[str | URL, str]:
    if explicit is not None:
        return explicit, "explicit"
    for env_name in DATABASE_ENV_CANDIDATES:
        value = os.getenv(env_name)
        if _supported_database_url(value):
            return value or "", env_name.lower()
    component_url = _database_url_from_components()
    if component_url is not None:
        return component_url
    if os.getenv("VERCEL") or os.getenv("VERCEL_ENV"):
        return VERCEL_FALLBACK_DATABASE_URL, "vercel_sqlite_fallback"
    return settings.database_url, "settings"


def _classify_database_configuration_error(error: Exception, database_url: str | URL) -> str:
    if not _supported_database_url(database_url):
        return "unsupported_scheme"
    if isinstance(database_url, str):
        normalized = normalize_database_url(database_url)
        if any(placeholder in normalized.upper() for placeholder in PASSWORD_PLACEHOLDERS):
            return "password_placeholder_present"
    message = str(error).lower()
    if "tenant or user not found" in message:
        return "pooler_tenant_or_user_not_found"
    if "password authentication failed" in message or "authentication failed" in message:
        return "authentication_failed"
    if "database" in message and "does not exist" in message:
        return "database_not_found"
    if "could not translate host name" in message or "name or service not known" in message:
        return "host_resolution_failed"
    if "network is unreachable" in message or "connection timed out" in message:
        return "connection_unreachable"
    if "connection refused" in message:
        return "connection_refused"
    if "remaining connection slots" in message or "too many connections" in message:
        return "connection_limit_reached"
    if "server closed the connection unexpectedly" in message:
        return "provider_connection_closed"
    if "ssl" in message and ("error" in message or "failed" in message):
        return "ssl_error"
    if "could not parse" in message or "invalid dsn" in message or "invalid url" in message:
        return "invalid_url_syntax"
    return f"connection_or_schema_failed_{type(error).__name__.lower()}"


def _initialize_deployment_schema(app: FastAPI) -> None:
    if app.state.database_source not in {"vercel_sqlite_fallback", "reroute_database_url"}:
        return
    with app.state.session_factory() as session:
        Base.metadata.create_all(session.get_bind())


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
    app.state.database_env_capabilities = _database_env_capabilities()
    try:
        app.state.session_factory = create_session_factory(effective_database_url)
        _initialize_deployment_schema(app)
    except Exception as error:
        app.state.session_factory = _database_not_configured
        app.state.database_configuration = "configuration_error"
        app.state.database_configuration_reason = _classify_database_configuration_error(
            error, effective_database_url
        )
    else:
        app.state.database_configuration = "ok"
        app.state.database_configuration_reason = (
            "ephemeral_fallback"
            if database_source == "vercel_sqlite_fallback"
            else "ok"
        )

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
                "database_env_capabilities": app.state.database_env_capabilities,
                "routers": app.state.router_statuses,
            },
        }

    return app


app = create_app()
