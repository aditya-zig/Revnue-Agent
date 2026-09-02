import sqlite3

import pytest

from scripts.genuine_testmode_provider_probe import (
    migrate,
    request_json,
    wait_for_health,
    wait_for_receipt_reconciliation,
)


def test_migrate_resolves_repo_paths_outside_repo_root(tmp_path, monkeypatch):
    database = tmp_path / "outside-repo.db"
    monkeypatch.chdir(tmp_path)
    migrate(f"sqlite:///{database}")
    with sqlite3.connect(database) as connection:
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    assert row is not None
    assert row[0]


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


def test_receipt_reconciliation_retries_until_visible(monkeypatch):
    expected = {
        "id": "order_probe_001",
        "receipt": "receipt_probe_001",
        "amount": 249900,
        "currency": "INR",
    }
    results = iter([None, None, expected])
    sleeps = []
    monkeypatch.setattr(
        "scripts.genuine_testmode_provider_probe.find_order_by_receipt",
        lambda *args, **kwargs: next(results),
    )
    monkeypatch.setattr(
        "scripts.genuine_testmode_provider_probe.time.sleep",
        lambda seconds: sleeps.append(seconds),
    )
    result, attempts = wait_for_receipt_reconciliation(
        "rzp_test_example", "example-secret", "receipt_probe_001", "order_probe_001"
    )
    assert result == expected
    assert attempts == 3
    assert sleeps == [0.25, 0.5]


def test_receipt_reconciliation_rejects_wrong_order(monkeypatch):
    monkeypatch.setattr(
        "scripts.genuine_testmode_provider_probe.find_order_by_receipt",
        lambda *args, **kwargs: {
            "id": "order_wrong",
            "receipt": "receipt_probe",
            "amount": 249900,
            "currency": "INR",
        },
    )
    with pytest.raises(RuntimeError, match="provider_receipt_resolved_to_different_order"):
        wait_for_receipt_reconciliation(
            "rzp_test_example", "example-secret", "receipt_probe", "order_expected"
        )
