from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool


def normalize_database_url(database_url: str) -> str:
    """Normalize provider URLs to SQLAlchemy's accepted PostgreSQL scheme."""
    if database_url.startswith("postgres://"):
        return "postgresql://" + database_url.removeprefix("postgres://")
    return database_url


def create_session_factory(database_url: str) -> sessionmaker[Session]:
    database_url = normalize_database_url(database_url)
    if database_url.startswith("sqlite"):
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
