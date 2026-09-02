import csv
from pathlib import Path

from scripts.genuine_testmode_prepare import prepare, read_test_credentials


def write_credentials(path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["Key Id", "Key Secret"])
        writer.writeheader()
        writer.writerow(
            {
                "Key Id": "rzp_test_exampleonly",
                "Key Secret": "example_test_secret_only",
            }
        )


def test_prepare_generates_private_webhook_secret(tmp_path):
    credentials = tmp_path / "credentials.csv"
    write_credentials(credentials)
    runtime = tmp_path / "runtime"
    report = prepare(credentials_file=credentials, runtime_dir=runtime)
    assert report["key_id_present"] is True
    assert report["key_mode"] == "TEST"
    assert report["key_secret_present"] is True
    assert report["webhook_secret_present"] is True
    assert report["webhook_secret_generated"] is True
    secret_file = runtime / "webhook-secret"
    assert secret_file.exists()
    assert len(secret_file.read_text().strip()) >= 24
    env_file = runtime / "genuine-testmode.env"
    assert env_file.exists()
    body = env_file.read_text()
    assert "REROUTE_RAZORPAY_KEY_ID=" in body
    assert "REROUTE_RAZORPAY_KEY_SECRET=" in body
    assert "REROUTE_RAZORPAY_WEBHOOK_SECRET=" in body


def test_prepare_reuses_webhook_secret(tmp_path):
    credentials = tmp_path / "credentials.csv"
    write_credentials(credentials)
    runtime = tmp_path / "runtime"
    first = prepare(credentials_file=credentials, runtime_dir=runtime)
    secret_before = (runtime / "webhook-secret").read_text()
    second = prepare(credentials_file=credentials, runtime_dir=runtime)
    secret_after = (runtime / "webhook-secret").read_text()
    assert first["webhook_secret_generated"] is True
    assert second["webhook_secret_generated"] is False
    assert secret_after == secret_before


def test_rejects_non_test_key(tmp_path):
    credentials = tmp_path / "credentials.csv"
    with credentials.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["Key Id", "Key Secret"])
        writer.writeheader()
        writer.writerow({"Key Id": "rzp_live_not_allowed", "Key Secret": "example"})
    try:
        read_test_credentials(credentials)
    except ValueError as error:
        assert "Test Mode" in str(error)
    else:
        raise AssertionError("live-mode key should have been rejected")
