import psycopg
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool


def _strip_wrapping_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1].strip()
    return value


def normalize_database_url(database_url: str) -> str:
    """Normalize common copied environment-variable forms for SQLAlchemy."""
    value = _strip_wrapping_quotes(database_url)
    for prefix in (
        "REROUTE_DATABASE_URL=",
        "DATABASE_URL=",
        "POSTGRES_URL=",
        "SUPABASE_DB_URL=",
        "SUPABASE_DATABASE_URL=",
    ):
        if value.startswith(prefix):
            value = _strip_wrapping_quotes(value.removeprefix(prefix))
            break
    if value.startswith("postgres://"):
        return "postgresql://" + value.removeprefix("postgres://")
    return value


def create_session_factory(database_url: str | URL) -> sessionmaker[Session]:
    if isinstance(database_url, str):
        database_url = normalize_database_url(database_url)
        is_sqlite = database_url.startswith("sqlite")
        engine_url: str | URL = database_url
        if database_url.startswith("postgresql://"):
            engine_url = "postgresql+psycopg://" + database_url.removeprefix("postgresql://")
    else:
        is_sqlite = database_url.get_backend_name() == "sqlite"
        engine_url = database_url

    if is_sqlite:
        engine = create_engine(
            engine_url,
            connect_args={"check_same_thread": False},
        )
    else:
        # Keep the driver as an explicit import so Vercel bundles it with the
        # function. Supabase transaction pooling owns connection pooling and
        # does not need psycopg's automatic server-side prepared statements.
        if psycopg.__name__ != "psycopg":
            raise RuntimeError("unexpected PostgreSQL driver")
        engine = create_engine(
            engine_url,
            poolclass=NullPool,
            pool_pre_ping=True,
            connect_args={"prepare_threshold": None},
        )
    return sessionmaker(bind=engine, expire_on_commit=False)
