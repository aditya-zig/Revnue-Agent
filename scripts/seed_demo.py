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
    from simulator.generator import generate_csv

    with session_factory() as session:
        imported, duplicates = import_csv(session, generate_csv())
        if duplicates:
            raise RuntimeError("the synthetic CSV must not contain duplicate events")
        detect_and_store_leaks(session)
        session.commit()

        action_time = datetime(2026, 8, 24, 5, tzinfo=UTC)

        hard_decline = make_eligible(session, "case_demo_hard_decline")
        try:
            execute_action(
                session,
                hard_decline,
                "retry",
                "demo-hard-decline-retry",
                action_time,
                21,
                8,
                unavailable_payment_link,
            )
        except PermissionError:
            pass

        provider_failure = make_eligible(session, "case_demo_provider_failure")
        try:
            execute_action(
                session,
                provider_failure,
                "payment_link",
                "demo-provider-failure",
                action_time,
                21,
                8,
                unavailable_payment_link,
            )
        except ProviderError:
            pass

        opted_out = make_eligible(session, "case_demo_opt_out")
        try:
            execute_action(
                session,
                opted_out,
                "contact",
                "demo-opt-out-contact",
                action_time,
                21,
                8,
                unavailable_payment_link,
            )
        except PermissionError:
            pass

        make_eligible(session, "case_demo_promise")

    print(f"Imported {imported} synthetic events into {database_url}.")
    print("Recorded hard-decline and opt-out blocks plus a payment-link provider failure.")


if __name__ == "__main__":
    main()
