from scripts.genuine_testmode_evidence import build_report


def dashboard(provider=None, case=None, events=None):
    return {
        "population": {
            "total": 999,
            "captured": 749,
            "failed": 250,
            "test_mode_events": 0,
            "latest_test_mode_payment": (
                {"payment_id": case["payment_id"]} if case else None
            ),
        },
        "provider_evidence": provider or {},
        "worklist": [case] if case else [],
        "timeline": [{"case_id": case["case_id"], "events": events or []}] if case else [],
    }


def test_empty_dashboard_is_not_ready():
    report = build_report(dashboard())
    assert report["local_signed_failure_ready"] is False
    assert report["local_signed_recovery_ready"] is False


def test_signed_failure_with_eligible_case_is_ready():
    case = {"case_id": "case-1", "payment_id": "pay-1", "state": "eligible", "ranked_actions": []}
    provider = {"present": True, "event_types": ["payment.failed"], "signed_event_count": 1}
    report = build_report(dashboard(provider, case))
    assert isinstance(report["signed_evidence"], dict)
    assert isinstance(report["recovery_case"], dict)
    assert report["signed_evidence"]["payment_failed_present"] is True
    assert report["recovery_case"]["state"] == "eligible"
    assert report["local_signed_failure_ready"] is True


def test_signed_recovery_requires_capture_and_test_mode_outcome():
    case = {
        "case_id": "case-1",
        "payment_id": "pay-1",
        "state": "recovered",
        "ranked_actions": ["payment_link"],
    }
    provider = {
        "present": True,
        "event_types": ["payment.failed", "payment.captured"],
        "signed_event_count": 2,
    }
    events = [
        {
            "kind": "action",
            "data": {
                "tool": "payment_link",
                "status": "completed",
                "provider_reference": "plink",
            },
        },
        {
            "kind": "outcome",
            "data": {
                "recovered": True,
                "recovered_amount": 249900,
                "source": "razorpay_test",
            },
        },
    ]
    report = build_report(dashboard(provider, case, events))
    assert isinstance(report["action"], dict)
    assert isinstance(report["outcome"], dict)
    assert report["action"]["present"] is True
    assert report["outcome"]["recovered"] is True
    assert report["local_signed_recovery_ready"] is True


def test_mock_outcome_does_not_count_as_genuine_recovery():
    case = {"case_id": "case-1", "payment_id": "pay-1", "state": "recovered", "ranked_actions": []}
    provider = {"present": True, "event_types": ["payment.failed", "payment.captured"]}
    events = [{"kind": "outcome", "data": {"recovered": True, "source": "mock"}}]
    report = build_report(dashboard(provider, case, events))
    assert isinstance(report["outcome"], dict)
    assert report["outcome"]["recovered"] is False
    assert report["local_signed_recovery_ready"] is False
