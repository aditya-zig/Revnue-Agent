import tomllib
from pathlib import Path

from app.main import create_app


REPO_ROOT = Path(__file__).resolve().parents[2]


def _health_payload(app) -> dict[str, object]:
    route = next(route for route in app.routes if getattr(route, "path", None) == "/health")
    return route.endpoint()


def test_vercel_entrypoint_targets_fastapi_app() -> None:
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["tool"]["vercel"]["entrypoint"] == "app.main:app"


def test_app_static_mount_does_not_depend_on_process_cwd(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    app = create_app(database_url="sqlite:///:memory:")
    static_routes = [route for route in app.routes if getattr(route, "path", None) == "/static"]
    assert static_routes
    payload = _health_payload(app)
    components = payload["components"]
    assert isinstance(components, dict)
    assert components["static"] == "ok"
    assert all(status == "ok" for status in components["routers"].values())


def test_health_survives_invalid_deployment_database_url() -> None:
    app = create_app(database_url="https://example.supabase.co")

    payload = _health_payload(app)

    assert payload["status"] == "ok"
    assert payload["ready"] is False
    components = payload["components"]
    assert isinstance(components, dict)
    assert components["database"] == "configuration_error"
