import pytest

from scripts.genuine_testmode_provider_probe import request_json, wait_for_health


def test_invalid_app_response(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b"[]"
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: Response())
    with pytest.raises(RuntimeError, match="app_response_invalid"):
        request_json("http://test")


def test_health_timeout(monkeypatch):
    monkeypatch.setattr(
        "scripts.genuine_testmode_provider_probe.request_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    with pytest.raises(RuntimeError, match="local_server_not_ready"):
        wait_for_health("http://test", timeout_seconds=0)
