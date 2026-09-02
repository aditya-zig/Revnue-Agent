from pathlib import Path

from scripts import genuine_testmode_webhook as webhook


def test_extract_public_url_supports_zrok_output():
    assert webhook.extract_public_url(
        'share ready frontendEndpoint: "https://demo.share.zrok.io"'
    ) == "https://demo.share.zrok.io"


def test_extract_public_url_returns_last_https_url():
    assert webhook.extract_public_url(
        "docs https://docs.zrok.io public https://demo.share.zrok.io/"
    ) == "https://demo.share.zrok.io"


def test_webhook_configuration_is_test_mode_and_exact_events(tmp_path, monkeypatch):
    monkeypatch.setattr(webhook, "WEBHOOK_SECRET_PATH", tmp_path / "webhook-secret")

    config = webhook.webhook_configuration("https://demo.example")

    assert config["mode"] == "TEST"
    assert config["url"] == "https://demo.example/api/v1/webhooks/razorpay"
    assert config["events"] == ["payment.failed", "payment.captured"]
    assert config["test_mode_otp"] == "754081"
    assert config["dashboard_action_required"] is True


def test_status_does_not_claim_provider_delivery_without_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(webhook, "PUBLIC_URL_PATH", tmp_path / "public-url")
    monkeypatch.setattr(webhook, "ZROK_PID_PATH", tmp_path / "zrok.pid")
    monkeypatch.setattr(webhook, "_provider_evidence", lambda: {"present": False, "signed_event_count": 0})

    result = webhook.status()

    assert result["running"] is False
    assert result["public_url"] is None
    assert result["provider_evidence"] == {"present": False, "signed_event_count": 0}


def test_stop_refuses_unrelated_process(tmp_path, monkeypatch):
    pid_path = tmp_path / "zrok.pid"
    pid_path.write_text("123\n")
    monkeypatch.setattr(webhook, "ZROK_PID_PATH", pid_path)
    monkeypatch.setattr(webhook, "_running", lambda pid: True)
    monkeypatch.setattr(webhook, "_is_zrok_share", lambda pid: False)

    result = webhook.stop()

    assert result == {
        "result": "error",
        "error": "zrok_pid_not_share_process",
        "pid": 123,
    }
    assert pid_path.exists()


def test_start_requires_enabled_zrok(tmp_path, monkeypatch):
    monkeypatch.setattr(webhook, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(webhook, "ZROK_PID_PATH", tmp_path / "zrok.pid")
    monkeypatch.setattr(webhook, "PUBLIC_URL_PATH", tmp_path / "public-url")
    monkeypatch.setattr(webhook, "demo_status", lambda: {"running": True, "health": True})
    monkeypatch.setattr(webhook, "_zrok_binary", lambda: "/fake/zrok2")
    monkeypatch.setattr(webhook, "_zrok_enabled", lambda binary: False)

    try:
        webhook.start()
    except RuntimeError as error:
        assert str(error) == "zrok_not_enabled"
    else:
        raise AssertionError("expected zrok_not_enabled")
