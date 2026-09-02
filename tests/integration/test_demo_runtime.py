import json

import pytest

from scripts import demo_runtime


def test_pid_reading_handles_missing_and_invalid(tmp_path, monkeypatch):
    monkeypatch.setattr(demo_runtime, "PID_PATH", tmp_path / "pid")
    assert demo_runtime.read_demo_pid() is None
    demo_runtime.PID_PATH.write_text("not-a-pid")
    assert demo_runtime.read_demo_pid() is None


def test_config_path_persistence(tmp_path, monkeypatch):
    monkeypatch.setattr(demo_runtime, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(demo_runtime, "CONFIG_PATH", tmp_path / "config.json")
    credentials = tmp_path / "keys.csv"
    credentials.write_text("not-read")
    config = demo_runtime.load_demo_config(credentials)
    assert config["credentials_file"] == str(credentials.resolve())
    assert json.loads((tmp_path / "config.json").read_text())["credentials_file"] == str(
        credentials.resolve()
    )


def test_missing_demo_config(tmp_path, monkeypatch):
    monkeypatch.setattr(demo_runtime, "CONFIG_PATH", tmp_path / "missing.json")
    monkeypatch.setattr(demo_runtime, "RUNTIME_DIR", tmp_path)
    with pytest.raises(RuntimeError, match="demo_credentials_not_configured"):
        demo_runtime.load_demo_config()


def test_status_while_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr(demo_runtime, "PID_PATH", tmp_path / "pid")
    monkeypatch.setattr(demo_runtime, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(demo_runtime, "SESSION_PATH", tmp_path / "session.json")
    config = {"host": "127.0.0.1", "port": 8000, "database_url": "sqlite:///./demo.db"}
    assert demo_runtime.demo_status(config)["running"] is False


def test_start_delegates_and_keep_db(tmp_path, monkeypatch):
    monkeypatch.setattr(demo_runtime, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(demo_runtime, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(demo_runtime, "PID_PATH", tmp_path / "pid")
    monkeypatch.setattr(demo_runtime, "SESSION_PATH", tmp_path / "session.json")
    credentials = tmp_path / "keys.csv"
    credentials.write_text("not-read")
    monkeypatch.setattr(
        demo_runtime,
        "demo_status",
        lambda config: {"running": False, "health": False},
    )
    calls = []
    def fake_start_session(**kwargs):
        calls.append(kwargs)
        return {"ready": True}

    monkeypatch.setattr(demo_runtime, "start_session", fake_start_session)
    demo_runtime.start_demo(credentials, reset_db=False)
    assert calls[0]["reset_db"] is False


def test_already_running_does_not_delegate(monkeypatch):
    monkeypatch.setattr(
        demo_runtime,
        "load_demo_config",
        lambda credentials=None: {"host": "127.0.0.1", "port": 8000, "database_url": "sqlite:///./demo.db"},
    )
    monkeypatch.setattr(
        demo_runtime,
        "demo_status",
        lambda config: {"running": True, "health": True, "pid": 1},
    )
    monkeypatch.setattr(demo_runtime, "start_session", lambda **kwargs: pytest.fail("duplicated"))
    assert demo_runtime.start_demo()["result"] == "already_running"


def test_stop_protects_unrelated_pid(tmp_path, monkeypatch):
    monkeypatch.setattr(demo_runtime, "PID_PATH", tmp_path / "pid")
    demo_runtime.PID_PATH.write_text("123")
    monkeypatch.setattr(demo_runtime, "process_is_running", lambda pid: True)
    monkeypatch.setattr(demo_runtime, "_is_reroute_process", lambda pid: False)
    assert demo_runtime.stop_demo()["error"] == "pid_not_reroute_process"
    assert demo_runtime.PID_PATH.exists()


def test_stop_cleans_stale_pid(tmp_path, monkeypatch):
    monkeypatch.setattr(demo_runtime, "PID_PATH", tmp_path / "pid")
    demo_runtime.PID_PATH.write_text("123")
    monkeypatch.setattr(demo_runtime, "process_is_running", lambda pid: False)
    assert demo_runtime.stop_demo()["result"] == "stale_pid_cleaned"
    assert not demo_runtime.PID_PATH.exists()


def test_start_cleans_stale_pid_before_delegating(tmp_path, monkeypatch):
    monkeypatch.setattr(demo_runtime, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(demo_runtime, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(demo_runtime, "PID_PATH", tmp_path / "pid")
    monkeypatch.setattr(demo_runtime, "SESSION_PATH", tmp_path / "session.json")
    credentials = tmp_path / "keys.csv"
    credentials.write_text("not-read")
    demo_runtime.PID_PATH.write_text("123")
    monkeypatch.setattr(
        demo_runtime,
        "demo_status",
        lambda config: {"running": False, "health": False},
    )
    monkeypatch.setattr(
        demo_runtime,
        "start_session",
        lambda **kwargs: {"ready": True},
    )
    demo_runtime.start_demo(credentials)
    assert not demo_runtime.PID_PATH.exists()
