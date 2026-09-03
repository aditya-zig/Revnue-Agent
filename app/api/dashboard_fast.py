from collections import defaultdict
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from sqlalchemy import select

from app.api import dashboard as legacy
from app.api.evaluations import get_published_evaluation
from app.db.tables import (
    ActionEvent,
    AuditEvent,
    Customer,
    Decision,
    LeakFinding,
    Outcome,
    PaymentEvent,
    PaymentException,
    RecoveryCase,
)
from app.domain.models import PolicyResponse
from app.leak_analysis import finding_sort_key
from app.policy import get_policy_configuration
from app.policy.evaluate import (
    CONTACT_ACTIONS,
    CUSTOMER_DIRECTED_ACTIONS,
    HARD_DECLINE_CODES,
    RECOVERY_ACTIONS,
    TERMINAL_CASE_STATES,
    _is_quiet_hour,
)

router = APIRouter(tags=["dashboard"])


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _policy_from_snapshot(
    *,
    case: RecoveryCase,
    customer: Customer | None,
    open_exception: bool,
    contact_count: int,
    error_codes: list[str | None],
    recent_action: bool,
    now: datetime,
    configuration,
) -> PolicyResponse:
    if configuration.kill_switch:
        return PolicyResponse(
            allowed_actions=[],
            blocked_reasons={action: ["kill_switch"] for action in RECOVERY_ACTIONS},
            policy_version=configuration.policy_version,
        )
    if case.state in TERMINAL_CASE_STATES:
        return PolicyResponse(
            allowed_actions=[],
            blocked_reasons={action: ["terminal_case"] for action in RECOVERY_ACTIONS},
            policy_version=configuration.policy_version,
        )

    blocked_reasons: dict[str, list[str]] = {}
    if customer is None:
        for action in CUSTOMER_DIRECTED_ACTIONS:
            blocked_reasons[action] = ["missing_identity"]
    elif not customer.consent:
        for action in CUSTOMER_DIRECTED_ACTIONS:
            blocked_reasons[action] = ["missing_consent"]

    if open_exception:
        for action in CUSTOMER_DIRECTED_ACTIONS:
            blocked_reasons.setdefault(action, []).append("payment_exception")

    if contact_count >= configuration.contact_limit:
        for action in CONTACT_ACTIONS:
            blocked_reasons.setdefault(action, []).append("contact_limit")

    local_hour = now.astimezone(ZoneInfo("Asia/Kolkata")).hour
    if _is_quiet_hour(
        local_hour,
        configuration.quiet_hours_start,
        configuration.quiet_hours_end,
    ):
        for action in CUSTOMER_DIRECTED_ACTIONS:
            blocked_reasons.setdefault(action, []).append("quiet_hours")

    if any(code and code.upper() in HARD_DECLINE_CODES for code in error_codes):
        blocked_reasons["retry"] = ["hard_decline"]

    if recent_action:
        for action in RECOVERY_ACTIONS:
            blocked_reasons.setdefault(action, []).append("action_limit")

    return PolicyResponse(
        allowed_actions=[action for action in RECOVERY_ACTIONS if action not in blocked_reasons],
        blocked_reasons=blocked_reasons,
        policy_version=configuration.policy_version,
    )


def _timeline_from_snapshot(
    case: RecoveryCase,
    payment_events: list[PaymentEvent],
    audit_events: list[AuditEvent],
    decisions: list[Decision],
    actions: list[ActionEvent],
    outcome: Outcome | None,
) -> dict:
    events = [
        {
            "kind": "raw event",
            "at": legacy._time(event.occurred_at),
            "data": legacy._payment(event),
        }
        for event in sorted(payment_events, key=lambda item: (item.occurred_at, item.event_id))
    ]
    events += [
        {
            "kind": "decision",
            "at": legacy._time(decision.created_at),
            "data": {
                "selected_action": decision.selected_action,
                "expected_value": decision.expected_value,
                "policy_version": decision.policy_version,
                "model_version": decision.model_version,
                "evidence": decision.reason_json.get("evidence", {}),
            },
        }
        for decision in sorted(decisions, key=lambda item: item.decision_id)
    ]
    events += [
        {
            "kind": "action",
            "at": legacy._time(action.executed_at),
            "data": legacy._action(action),
        }
        for action in sorted(actions, key=lambda item: item.executed_at)
    ]
    events += [
        {
            "kind": "audit",
            "at": legacy._time(event.created_at),
            "data": {"type": event.event_type, "payload": event.payload},
        }
        for event in sorted(audit_events, key=lambda item: item.created_at)
    ]
    if outcome:
        events.append(
            {
                "kind": "outcome",
                "at": legacy._time(outcome.resolved_at),
                "data": {
                    "recovered": outcome.recovered,
                    "recovered_amount": outcome.recovered_amount,
                    "source": outcome.source,
                },
            }
        )
    events.sort(key=lambda event: (event["at"] is None, event["at"] or ""))
    return {"case_id": case.case_id, "events": events}


def _events_for_case(
    case: RecoveryCase,
    by_obligation: dict[str, list[PaymentEvent]],
    by_payment: dict[str, list[PaymentEvent]],
) -> list[PaymentEvent]:
    if case.obligation_reference:
        return by_obligation.get(case.obligation_reference, [])
    return by_payment.get(case.payment_id, [])


@router.get("/api/v1/dashboard")
def get_dashboard_fast(request: Request) -> dict:
    """Render the dashboard with batched reads instead of per-case DB queries."""
    with request.app.state.session_factory() as session:
        configuration = get_policy_configuration(session, request.app.state)
        findings = session.scalars(select(LeakFinding)).all()
        findings.sort(key=finding_sort_key)
        cases = session.scalars(select(RecoveryCase).order_by(RecoveryCase.case_id)).all()
        exceptions = session.scalars(
            select(PaymentException).order_by(PaymentException.opened_at.desc())
        ).all()
        payments = session.scalars(select(PaymentEvent)).all()
        customers = session.scalars(select(Customer)).all()
        decisions = session.scalars(select(Decision)).all()
        actions = session.scalars(select(ActionEvent)).all()
        outcomes = session.scalars(select(Outcome)).all()
        audits = session.scalars(select(AuditEvent)).all()

        customers_by_id = {customer.customer_id: customer for customer in customers}
        outcomes_by_case = {outcome.case_id: outcome for outcome in outcomes}
        open_exceptions_by_case = {
            exception.case_id: exception.opened_at.isoformat()
            for exception in exceptions
            if exception.state == "open"
        }

        payments_by_obligation: dict[str, list[PaymentEvent]] = defaultdict(list)
        payments_by_payment: dict[str, list[PaymentEvent]] = defaultdict(list)
        for payment in payments:
            if payment.obligation_reference:
                payments_by_obligation[payment.obligation_reference].append(payment)
            payments_by_payment[payment.payment_id].append(payment)

        decisions_by_case: dict[str, list[Decision]] = defaultdict(list)
        for decision in decisions:
            decisions_by_case[decision.case_id].append(decision)

        actions_by_case: dict[str, list[ActionEvent]] = defaultdict(list)
        for action in actions:
            actions_by_case[action.case_id].append(action)

        audits_by_case: dict[str, list[AuditEvent]] = defaultdict(list)
        for audit in audits:
            audits_by_case[audit.case_id].append(audit)

        now = request.app.state.policy_now()
        cutoff = now.astimezone(UTC) - timedelta(hours=24)
        worklist: list[dict] = []
        timelines: list[dict] = []

        for case in cases:
            case_payments = _events_for_case(case, payments_by_obligation, payments_by_payment)
            evidence_events = sorted(
                case_payments,
                key=lambda item: (item.occurred_at, item.event_id),
                reverse=True,
            )
            payment = evidence_events[0] if evidence_events else None
            evidence_providers: list[str | None] = [
                event.provider for event in evidence_events
            ] or [None]
            customer = customers_by_id.get(case.customer_id) if case.customer_id else None

            case_decisions = decisions_by_case.get(case.case_id, [])
            decision = max(case_decisions, key=lambda item: item.decision_id, default=None)
            case_actions = actions_by_case.get(case.case_id, [])
            action = max(case_actions, key=lambda item: item.executed_at, default=None)
            contact_count = sum(item.tool in CONTACT_ACTIONS for item in case_actions)
            recent_action = any(
                item.status != "failed" and _as_utc(item.executed_at) >= cutoff
                for item in case_actions
            )
            policy = _policy_from_snapshot(
                case=case,
                customer=customer,
                open_exception=case.case_id in open_exceptions_by_case,
                contact_count=contact_count,
                error_codes=[event.error_code for event in case_payments],
                recent_action=recent_action,
                now=now,
                configuration=configuration,
            )
            ranked = (
                request.app.state.recovery_model.rank(case, customer, policy.allowed_actions)
                if case.state == "eligible"
                else []
            )
            if decision is not None:
                expected_value = decision.expected_value
            elif ranked:
                expected_value = int(ranked[0]["expected_net_value"])
            else:
                expected_value = None

            item = {
                "case_id": case.case_id,
                "payment_id": case.payment_id,
                "obligation_reference": case.obligation_reference,
                "customer_id": case.customer_id,
                "amount_at_risk": case.amount_at_risk,
                "state": case.state,
                "opened_at": case.opened_at.isoformat() if case.opened_at else None,
                "evidence": legacy._payment(payment) if payment else None,
                "evidence_providers": evidence_providers,
                "selected_action": (
                    decision.selected_action if decision else action.tool if action else None
                ),
                "expected_value": expected_value,
                "policy": policy.model_dump(),
                "open_payment_exception": case.case_id in open_exceptions_by_case,
                "open_payment_exception_at": open_exceptions_by_case.get(case.case_id),
                "contact_budget": max(0, configuration.contact_limit - contact_count),
                "owner": (
                    "business_owner" if case.state == "escalated" else "operations_worker"
                ),
                "blocked_reasons": policy.blocked_reasons,
                "customer": (
                    {
                        "consent": customer.consent,
                        "tenure_days": customer.tenure_days,
                        "successful_payments": customer.successful_payments,
                        "prior_failures": customer.prior_failures,
                    }
                    if customer
                    else None
                ),
                "ranked_actions": ranked,
                "human_review": {
                    "allowed_actions": policy.allowed_actions,
                    "can_execute": case.state == "eligible" and bool(policy.allowed_actions),
                },
            }
            worklist.append(item)
            timelines.append(
                _timeline_from_snapshot(
                    case,
                    case_payments,
                    audits_by_case.get(case.case_id, []),
                    case_decisions,
                    case_actions,
                    outcomes_by_case.get(case.case_id),
                )
            )

        worklist.sort(key=legacy._worklist_sort_key)
        revenue_at_risk = sum(
            case.amount_at_risk for case in cases if case.state not in {"recovered", "stopped"}
        )
        revenue_sources = [
            provider
            for item in worklist
            if item["state"] not in {"recovered", "stopped"}
            for provider in item["evidence_providers"]
        ]
        revenue_claim = legacy._claim_tag_for_providers(revenue_sources)
        top_recoverable = findings[0].recoverable_impact if findings else 0
        test_mode_value = sum(
            outcome.recovered_amount for outcome in outcomes if outcome.source == "razorpay_test"
        )

        payment_total = len(payments)
        payment_captured = sum(payment.status == "captured" for payment in payments)
        payment_failed = sum(payment.status == "failed" for payment in payments)
        test_mode_payments = [
            payment for payment in payments if payment.provider == "razorpay_test"
        ]
        latest_test_mode_payment = max(
            test_mode_payments,
            key=lambda item: (item.occurred_at, item.event_id),
            default=None,
        )
        signed_test_mode = sorted(
            [
                payment
                for payment in test_mode_payments
                if payment.raw_body is not None and bool(payment.raw_hash)
            ],
            key=lambda item: (item.occurred_at, item.event_id),
            reverse=True,
        )
        provider_evidence = legacy._signed_test_mode_evidence(session, signed_test_mode)
        top_finding = legacy._finding(session, findings[0]) if findings else None

        mock_inbox = [
            legacy._action(action)
            for action in sorted(actions, key=lambda item: item.executed_at, reverse=True)
            if action.tool in CONTACT_ACTIONS
        ]

        return {
            "provider_evidence": provider_evidence,
            "population": {
                "total": payment_total,
                "captured": payment_captured,
                "failed": payment_failed,
                "failure_rate": payment_failed / payment_total if payment_total else 0.0,
                "test_mode_events": len(test_mode_payments),
                "latest_test_mode_payment": (
                    legacy._payment(latest_test_mode_payment)
                    if latest_test_mode_payment is not None
                    else None
                ),
            },
            "executive": {
                "top_leak": top_finding,
                "revenue_at_risk": revenue_at_risk,
                "revenue_at_risk_claim_tag": revenue_claim,
                "estimated_value": top_recoverable,
                "test_mode_value": test_mode_value,
                "open_cases": sum(
                    item["state"] not in {"recovered", "closed", "stopped"}
                    for item in worklist
                ),
            },
            "investigation": top_finding,
            "worklist": worklist,
            "timeline": timelines,
            "payment_exceptions": [
                legacy._payment_exception(exception) for exception in exceptions
            ],
            "policy_settings": legacy._policy_settings(configuration),
            "evaluation": get_published_evaluation(),
            "mock_inbox": mock_inbox,
        }
