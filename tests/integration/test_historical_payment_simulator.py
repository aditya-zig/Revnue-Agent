import csv
import hashlib
from collections import Counter
from io import StringIO

import pytest
from sqlalchemy import select

from app.db.tables import LeakFinding, PaymentEvent, RecoveryCase
from app.ingestion.csv_loader import import_csv
from app.leak_analysis import detect_and_store_leaks, finding_sort_key
from app.main import create_app
from simulator.generator import generate_csv

ISSUE_47_SEED = 47
ISSUE_47_COUNT = 999


@pytest.fixture
def app(database_url):
    return create_app(database_url=database_url)


def _rows() -> list[dict[str, str]]:
    return list(csv.DictReader(StringIO(generate_csv(seed=ISSUE_47_SEED))))


def test_issue_47_population_has_explicit_counts_and_cohort_rates():
    content = generate_csv(seed=ISSUE_47_SEED, event_count=ISSUE_47_COUNT)
    rows = list(csv.DictReader(StringIO(content)))
    status_counts = Counter(row["status"] for row in rows)
    method_counts = Counter(row["method"] for row in rows)

    assert len(rows) == ISSUE_47_COUNT
    assert hashlib.sha256(content.encode()).hexdigest() == (
        "b31cf603e91fcde0140c4c110dfc37202c116d703cbb574af8137b93ff047cc1"
    )
    assert status_counts == {"failed": 250, "captured": 749}
    assert method_counts == {"upi": 450, "card": 275, "netbanking": 274}
    assert len({row["event_id"] for row in rows}) == ISSUE_47_COUNT
    assert len({row["payment_id"] for row in rows}) == ISSUE_47_COUNT
    assert [row["event_id"] for row in rows[:992]] == [
        f"demo_event_{index:05d}" for index in range(992)
    ]
    assert [row["event_id"] for row in rows[-7:]] == [
        "demo_hard_decline",
        "demo_provider_failure",
        "demo_opt_out",
        "demo_promise",
        "demo_eligible",
        "demo_isolated_a",
        "demo_isolated_b",
    ]

    rates = {
        method: (
            sum(row["status"] == "failed" for row in rows if row["method"] == method),
            sum(row["method"] == method for row in rows),
        )
        for method in method_counts
    }
    assert rates == {"upi": (225, 450), "card": (13, 275), "netbanking": (12, 274)}
    assert 250 / ISSUE_47_COUNT == pytest.approx(0.2502502502502503)
    assert 749 / ISSUE_47_COUNT == pytest.approx(0.7497497497497497)
    assert 0.20 <= 250 / ISSUE_47_COUNT <= 0.30
    assert rates["upi"][0] / rates["upi"][1] == pytest.approx(0.50)
    assert rates["card"][0] / rates["card"][1] < 0.15
    assert rates["netbanking"][0] / rates["netbanking"][1] < 0.15


def test_issue_47_regeneration_and_import_are_deterministic_idempotent_and_provenanced(app):
    first_content = generate_csv(seed=ISSUE_47_SEED, event_count=ISSUE_47_COUNT)
    second_content = generate_csv(seed=ISSUE_47_SEED, event_count=ISSUE_47_COUNT)
    assert first_content == second_content

    with app.state.session_factory() as session:
        assert import_csv(session, first_content) == (999, 0)
        session.commit()
        assert import_csv(session, second_content) == (0, 999)
        session.commit()

        assert len(session.scalars(select(PaymentEvent)).all()) == 999
        assert len(session.scalars(select(RecoveryCase)).all()) == 250

        generated_event = session.get(PaymentEvent, "demo_event_00000")
        assert generated_event is not None
        assert generated_event.provider == "csv_import"
        assert generated_event.provider_event_id == generated_event.event_id
        assert generated_event.raw_body is None
        expected_hash = hashlib.sha256(
            str(
                sorted(
                    next(
                        row for row in _rows() if row["event_id"] == generated_event.event_id
                    ).items()
                )
            ).encode()
        ).hexdigest()
        assert generated_event.raw_hash == expected_hash

        captured_event = next(
            event
            for event in session.scalars(select(PaymentEvent)).all()
            if event.status == "captured"
        )
        assert captured_event.provider == "csv_import"
        assert captured_event.obligation_reference is not None

        isolated_event = session.get(PaymentEvent, "demo_isolated_a")
        assert isolated_event is not None
        assert isolated_event.obligation_reference is None
        assert session.get(RecoveryCase, "case_demo_isolated_a") is not None
        assert session.get(RecoveryCase, "case_demo_isolated_b") is not None


def test_issue_47_real_detector_ranks_upi_by_recoverable_impact(app):
    with app.state.session_factory() as session:
        imported, duplicates = import_csv(
            session, generate_csv(seed=ISSUE_47_SEED, event_count=ISSUE_47_COUNT)
        )
        findings = detect_and_store_leaks(session)
        session.commit()

        assert (imported, duplicates) == (999, 0)
        assert len(findings) == 37
        assert all(finding.evidence_json["support"] >= 3 for finding in findings)

        top = findings[0]
        assert top.cohort_filter == {"dimension": "method", "value": "upi"}
        assert len(top.evidence_json["event_ids"]) == 450
        upi_event_ids = {
            event.event_id
            for event in session.scalars(select(PaymentEvent)).all()
            if event.method == "upi"
        }
        assert set(top.evidence_json["event_ids"]) == upi_event_ids
        assert top.evidence_json["support"] == 450
        assert top.evidence_json["failure_count"] == 225
        assert top.evidence_json["attempted_value"] == 93355200
        assert top.evidence_json["failed_value"] == 46177700
        assert top.evidence_json["unresolved_value"] == 46177700
        assert top.evidence_json["recovery_probability"] == 0.5
        assert top.evidence_json["data_quality_warnings"] == []
        assert top.baseline_rate == pytest.approx(250 / 999)
        assert top.observed_rate == pytest.approx(0.5)
        assert top.impact == 23315438
        assert top.recoverable_impact == 11657719
        assert top.recoverable_impact > findings[1].recoverable_impact
        assert findings[1].cohort_filter == {"dimension": "error_source", "value": "bank"}
        assert findings[1].recoverable_impact == 10416686

        # The detector's returned order is the persisted recoverable-impact
        # ranking, not a fixture assertion about a ratio or source string.
        persisted = session.scalars(select(LeakFinding)).all()
        persisted.sort(key=finding_sort_key)
        assert persisted[0].cohort_filter == top.cohort_filter
        assert persisted[0].recoverable_impact == top.recoverable_impact


def test_issue_47_default_is_the_revised_population_not_rejected_failure_shape():
    rows = _rows()
    failed = sum(row["status"] == "failed" for row in rows)
    captured = sum(row["status"] == "captured" for row in rows)

    # This explicit guard prevents the old rejected 800-failure/199-success
    # population from returning as the default fixture.
    assert (failed, captured) == (250, 749)
    assert failed != 800
