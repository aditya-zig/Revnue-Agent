from pathlib import Path
import tomllib

from app.main import create_app


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_vercel_entrypoint_targets_fastapi_app() -> None:
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["tool"]["vercel"]["entrypoint"] == "app.main:app"


def test_app_static_mount_does_not_depend_on_process_cwd(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    app = create_app(database_url="sqlite:///:memory:")
    static_routes = [route for route in app.routes if getattr(route, "path", None) == "/static"]
    assert static_routes
