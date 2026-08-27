import sys
from datetime import UTC, datetime
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPOSITORY_ROOT))

def unavailable_payment_link(amount: int, idempotency_key: str) -> str:
    raise RuntimeError("demo payment-link provider is not configured")


def main() -> None:
    from app.core.config import Settings
    from app.db.session import create_session_factory
    from app.db.tables import RecoveryCase
    from app.domain.enums import CaseState
    from app.domain.state_machine import transition_case
    from app.ingestion.csv_loader import import_csv
    from app.leak_analysis import detect_and_store_leaks
    from app.recovery.actions import ProviderError, execute_action

    def make_eligible(session, case_id: str) -> RecoveryCase:
        case = session.get(RecoveryCase, case_id)
        if case is None:
            raise RuntimeError(f"missing synthetic case {case_id}")
        transition_case(session, case, CaseState.INVESTIGATED)
        transition_case(session, case, CaseState.ELIGIBLE)
        session.commit()
        return case

    database_url = Settings().database_url
    session_factory = create_session_factory(database_url)
    # SyntheticCorpus is deterministic and separate from EvaluationComparison (30x30 SIMULATED).
    # Use generator for >=500 events with PaymentObligation identity; fallback to demo CSV for offline small demo.
    try:
        from simulator.generator import generate_csv

        csv_content = generate_csv(seed=7, event_count=500)
        source = "simulator.generator"
    except Exception:
        csv_path = REPOSITORY_ROOT / "demo" / "payment_events.csv"
        csv_content = csv_path.read_text()
        source = "demo/payment_events.csv"

    with session_factory() as session:
        imported, duplicates = import_csv(session, csv_content)
        # idempotent: duplicates are expected on re-seed, not an error for clean demo
        detect_and_store_leaks(session)
        session.commit()

        # named edge cases with deterministic Policy/AuditEvent behavior
        # case ids derive from obligation_reference when present (case_<obligation>), else case_<payment_id>
        edge_cases = {
            "hard_decline": ("case_order_hard_decline", "retry", "demo-hard-decline-retry"),
            "provider_failure": ("case_order_provider_failure", "payment_link", "demo-provider-failure"),
            "opt_out": ("case_order_opt_out", "contact", "demo-opt-out-contact"),
            "promise": ("case_order_promise", "promise", "demo-promise-contact"),
            "eligible": ("case_order_eligible", None, None),
        }
        # make all edge cases eligible so Policy can be evaluated; keep eligible unacted without action
        for name, (case_id, action, key) in edge_cases.items():
            try:
                case = make_eligible(session, case_id)
            except RuntimeError:
                continue
            if action is None or key is None:
                continue
            try:
                execute_action(
                    session,
                    case,
                    action,
                    key,
                    datetime(2026, 8, 24, 5, tzinfo=UTC),
                    21,
                    8,
                    unavailable_payment_link,
                )
            except (PermissionError, ProviderError):
                pass

    print(f"Imported {imported} synthetic events ({source}) into {database_url} (duplicates={duplicates}).")
    print("Recorded hard-decline block, provider failure, opt-out block, promise, and eligible unacted.")


if __name__ == "__main__":
    main()
