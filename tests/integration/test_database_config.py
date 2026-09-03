from sqlalchemy.pool import NullPool

from app.db import session as db_session


def test_normalize_database_url_accepts_supabase_postgres_scheme() -> None:
    assert (
        db_session.normalize_database_url(
            "postgres://postgres.example:secret@pooler.supabase.com:6543/postgres"
        )
        == "postgresql://postgres.example:secret@pooler.supabase.com:6543/postgres"
    )


def test_normalize_database_url_keeps_postgresql_scheme() -> None:
    assert (
        db_session.normalize_database_url(
            "postgresql://postgres.example:secret@pooler.supabase.com:6543/postgres"
        )
        == "postgresql://postgres.example:secret@pooler.supabase.com:6543/postgres"
    )


def test_postgres_session_factory_uses_psycopg_and_null_pool(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_create_engine(url: str, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(db_session, "create_engine", fake_create_engine)

    db_session.create_session_factory(
        "postgres://postgres.example:secret@pooler.supabase.com:6543/postgres"
    )

    assert captured["url"] == (
        "postgresql+psycopg://postgres.example:secret@pooler.supabase.com:6543/postgres"
    )
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["poolclass"] is NullPool
    assert kwargs["pool_pre_ping"] is True


def test_sqlite_session_factory_keeps_thread_compatibility(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_create_engine(url: str, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(db_session, "create_engine", fake_create_engine)

    db_session.create_session_factory("sqlite:///./reroute.db")

    assert captured["url"] == "sqlite:///./reroute.db"
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["connect_args"] == {"check_same_thread": False}
