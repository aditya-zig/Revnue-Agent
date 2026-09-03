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
    """Normalize copied deployment URLs and select the explicit psycopg3 driver."""
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
        return "postgresql+psycopg://" + value.removeprefix("postgres://")
    if value.startswith("postgresql://"):
        return "postgresql+psycopg://" + value.removeprefix("postgresql://")
    return value


def create_session_factory(database_url: str | URL) -> sessionmaker[Session]:
    if isinstance(database_url, str):
        database_url = normalize_database_url(database_url)
        is_sqlite = database_url.startswith("sqlite")
    else:
        is_sqlite = database_url.get_backend_name() == "sqlite"

    if is_sqlite:
        engine = create_engine(
            database_url,
            connect_args={"check_same_thread": False},
        )
    else:
        # Vercel/serverless instances should not hold an application-side
        # PostgreSQL pool. Supabase's transaction pooler owns pooling instead.
        engine = create_engine(
            database_url,
            poolclass=NullPool,
            pool_pre_ping=True,
        )
    return sessionmaker(bind=engine, expire_on_commit=False)
