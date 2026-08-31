from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select

from app.api.evaluations import get_published_evaluation
from app.db.tables import (
    ActionEvent,
    AuditEvent,
    Decision,
    FindingAnalysis,
    LeakFinding,
    Outcome,
    PaymentEvent,
    PaymentException,
    RecoveryCase,
)
from app.finding_analysis import analysis_response
from app.leak_analysis import finding_sort_key
from app.policy import evaluate_policy, get_policy_configuration

router = APIRouter(tags=["dashboard"])
DASHBOARD_HTML = (
    Path(__file__).resolve().parent.parent / "templates" / "dashboard.html"
).read_text(encoding="utf-8")


def _worklist_sort_key(item: dict) -> tuple[int, int, str]:
    # ADR 0006 order: escalations -> aged PaymentExceptions -> eligible by
    # expected net value -> investigated by age
    state = item["state"]
    if state == "escalated":
        rank = 0
        # aged: older opened_at first
        secondary = item.get("opened_at") or ""
        return (rank, 0, secondary)
    if item["open_payment_exception_at"]:
        return (1, 0, item["open_payment_exception_at"])
    if state == "eligible":
        rank = 2
        # eligible by expected net value descending (negate for ascending sort), then age
        ev = item.get("expected_value")
        # use negative for descending; None -> lowest priority
        neg_ev = -(ev or -1_000_000_000)
        return (rank, neg_ev, item.get("opened_at") or "")
    if state == "investigated":
        rank = 3
        return (rank, 0, item.get("opened_at") or "")
    # others like detected
    return (4, 0, item.get("opened_at") or "")


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard_page() -> str:
    return DASHBOARD_HTML


@router.get("/api/v1/dashboard")
def get_dashboard(request: Request) -> dict:
    with request.app.state.session_factory() as session:
        configuration = get_policy_configuration(session, request.app.state)
        findings = session.scalars(select(LeakFinding)).all()
        findings.sort(key=finding_sort_key)
        cases = session.scalars(select(RecoveryCase).order_by(RecoveryCase.case_id)).all()
        exceptions = session.scalars(
            select(PaymentException).order_by(PaymentException.opened_at.desc())
        ).all()
        open_exceptions_by_case = {
            exception.case_id: exception.opened_at.isoformat()
            for exception in exceptions
            if exception.state == "open"
        }
        worklist = [
            _case_summary(
                session,
                case,
                request,
                configuration,
                open_exceptions_by_case.get(case.case_id),
            )
            for case in cases
        ]
        # Worklist ordering follows ADR 0006.
        worklist.sort(key=lambda item: _worklist_sort_key(item))
        # Executive measures per ADR 0006: one ClaimTag per figure
        # Revenue at Risk = sum amount_at_risk where state not recovered
        # Estimated Recoverable = single top LeakFinding recoverable_impact (ESTIMATED)
        # Actual Recovered = sum Outcome where source=razorpay_test (TEST MODE)
        # Simulated Recovery is in evaluation (SIMULATED) - not computed here
        revenue_at_risk = sum(
            case.amount_at_risk for case in cases if case.state not in {"recovered", "stopped"}
        )
        top_recoverable = findings[0].recoverable_impact if findings else 0
        estimated_value = top_recoverable
        outcomes = session.scalars(select(Outcome)).all()
        test_mode_value = sum(
            outcome.recovered_amount for outcome in outcomes if outcome.source == "razorpay_test"
        )
        # Keep 0 when no razorpay_test outcomes exist rather than summing all;
        # this preserves ClaimTag discipline.

        return {
            "executive": {
                "top_leak": _finding(session, findings[0]) if findings else None,
                "revenue_at_risk": revenue_at_risk,
                "estimated_value": estimated_value,
                "test_mode_value": test_mode_value,
                "open_cases": sum(
                    case["state"] not in {"recovered", "closed", "stopped"} for case in worklist
                ),
            },
            "investigation": _finding(session, findings[0]) if findings else None,
            "worklist": worklist,
            "timeline": [_timeline(session, case) for case in cases],
            "payment_exceptions": [_payment_exception(exception) for exception in exceptions],
            "policy_settings": _policy_settings(configuration),
            "evaluation": get_published_evaluation(),
            "mock_inbox": [
                _action(action)
                for action in session.scalars(
                    select(ActionEvent)
                    .where(ActionEvent.tool.in_(["contact", "promise"]))
                    .order_by(ActionEvent.executed_at.desc())
                )
            ],
        }


def _case_summary(
    session, case: RecoveryCase, request: Request, configuration, open_exception_at: str | None
) -> dict:
    if getattr(case, "obligation_reference", None):
        payment = session.scalar(
            select(PaymentEvent)
            .where(PaymentEvent.obligation_reference == case.obligation_reference)
            .order_by(PaymentEvent.occurred_at.desc())
        )
    else:
        payment = session.scalar(
            select(PaymentEvent)
            .where(PaymentEvent.payment_id == case.payment_id)
            .order_by(PaymentEvent.occurred_at.desc())
        )
    decision = session.scalar(
        select(Decision)
        .where(Decision.case_id == case.case_id)
        .order_by(Decision.decision_id.desc())
    )
    action = session.scalar(
        select(ActionEvent)
        .where(ActionEvent.case_id == case.case_id)
        .order_by(ActionEvent.executed_at.desc())
    )
    policy = evaluate_policy(
        session,
        case,
        request.app.state.policy_now(),
        configuration.quiet_hours_start,
        configuration.quiet_hours_end,
        configuration.kill_switch,
        configuration.contact_limit,
        configuration.policy_version,
    )
    # Prefer persisted Decision for worklist sorting; otherwise rank eligible
    # cases via RecoveryModel.
    from app.db.tables import Customer

    customer = session.get(Customer, case.customer_id) if case.customer_id else None
    if decision is not None:
        expected_value = decision.expected_value
    elif case.state == "eligible":
        ranked = request.app.state.recovery_model.rank(case, customer, policy.allowed_actions)
        expected_value = int(ranked[0]["expected_net_value"]) if ranked else None
    else:
        expected_value = None
    contact_count = session.scalar(
        select(func.count())
        .select_from(ActionEvent)
        .where(
            ActionEvent.case_id == case.case_id,
            ActionEvent.tool.in_(["contact", "promise"]),
        )
    )
    return {
        "case_id": case.case_id,
        "payment_id": case.payment_id,
        "obligation_reference": getattr(case, "obligation_reference", None),
        "customer_id": case.customer_id,
        "amount_at_risk": case.amount_at_risk,
        "state": case.state,
        "opened_at": case.opened_at.isoformat() if case.opened_at else None,
        "evidence": _payment(payment) if payment else None,
        "selected_action": (
            decision.selected_action if decision else action.tool if action else None
        ),
        "expected_value": expected_value,
        "policy": policy.model_dump(),
        "open_payment_exception": open_exception_at is not None,
        "open_payment_exception_at": open_exception_at,
        "contact_budget": max(0, configuration.contact_limit - (contact_count or 0)),
        "owner": "business_owner" if case.state == "escalated" else "operations_worker",
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
        "ranked_actions": (
            request.app.state.recovery_model.rank(case, customer, policy.allowed_actions)
            if case.state == "eligible"
            else []
        ),
        "human_review": {
            "allowed_actions": policy.allowed_actions,
            "can_execute": case.state == "eligible" and bool(policy.allowed_actions),
        },
    }


def _timeline(session, case: RecoveryCase) -> dict:
    if getattr(case, "obligation_reference", None):
        payment_events = session.scalars(
            select(PaymentEvent)
            .where(PaymentEvent.obligation_reference == case.obligation_reference)
            .order_by(PaymentEvent.occurred_at)
        ).all()
    else:
        payment_events = session.scalars(
            select(PaymentEvent)
            .where(PaymentEvent.payment_id == case.payment_id)
            .order_by(PaymentEvent.occurred_at)
        ).all()
    audit_events = session.scalars(
        select(AuditEvent).where(AuditEvent.case_id == case.case_id).order_by(AuditEvent.created_at)
    ).all()
    decisions = session.scalars(
        select(Decision).where(Decision.case_id == case.case_id).order_by(Decision.decision_id)
    ).all()
    actions = session.scalars(
        select(ActionEvent)
        .where(ActionEvent.case_id == case.case_id)
        .order_by(ActionEvent.executed_at)
    ).all()
    outcome = session.scalar(select(Outcome).where(Outcome.case_id == case.case_id))
    events = [
        {"kind": "raw event", "at": _time(event.occurred_at), "data": _payment(event)}
        for event in payment_events
    ]
    events += [
        {
            "kind": "decision",
            "at": _time(decision.created_at),
            "data": {
                "selected_action": decision.selected_action,
                "expected_value": decision.expected_value,
                "policy_version": decision.policy_version,
                "model_version": decision.model_version,
                "evidence": decision.reason_json.get("evidence", {}),
            },
        }
        for decision in decisions
    ]
    events += [
        {"kind": "action", "at": _time(action.executed_at), "data": _action(action)}
        for action in actions
    ]
    events += [
        {
            "kind": "audit",
            "at": _time(event.created_at),
            "data": {"type": event.event_type, "payload": event.payload},
        }
        for event in audit_events
    ]
    if outcome:
        events.append(
            {
                "kind": "outcome",
                "at": _time(outcome.resolved_at),
                "data": {
                    "recovered": outcome.recovered,
                    "recovered_amount": outcome.recovered_amount,
                    "source": outcome.source,
                },
            }
        )
    events.sort(key=lambda event: (event["at"] is None, event["at"] or ""))
    return {"case_id": case.case_id, "events": events}


def _finding(session, finding: LeakFinding) -> dict:
    analysis = session.scalar(
        select(FindingAnalysis)
        .where(FindingAnalysis.source_finding_id == finding.finding_id)
        .order_by(FindingAnalysis.created_at.desc(), FindingAnalysis.analysis_id.desc())
    )
    return {
        "finding_id": finding.finding_id,
        "cohort_filter": finding.cohort_filter,
        "recoverable_impact": finding.recoverable_impact,
        "confidence": finding.confidence,
        "evidence": finding.evidence_json,
        "analysis": analysis_response(analysis) if analysis else None,
    }


def _payment(payment: PaymentEvent) -> dict:
    return {
        "event_id": payment.event_id,
        "event_type": payment.event_type,
        "status": payment.status,
        "amount": payment.amount,
        "method": payment.method,
        "error_reason": payment.error_reason,
        "obligation_reference": getattr(payment, "obligation_reference", None),
        "payment_id": payment.payment_id,
        "provider_event_id": payment.provider_event_id,
        "raw_hash": payment.raw_hash,
    }


def _action(action: ActionEvent) -> dict:
    return {
        "case_id": action.case_id,
        "tool": action.tool,
        "status": action.status,
        "provider_reference": action.provider_reference,
        "idempotency_key": action.idempotency_key,
        "reply": action.reply,
        "executed_at": _time(action.executed_at),
    }


def _payment_exception(exception: PaymentException) -> dict:
    return {
        "exception_id": exception.exception_id,
        "case_id": exception.case_id,
        "kind": exception.kind,
        "state": exception.state,
        "evidence": exception.evidence_json,
        "resolution": exception.resolution,
        "resolution_evidence": exception.resolution_evidence_json,
    }


def _policy_settings(configuration) -> dict:
    return {
        "version": configuration.version,
        "policy_version": configuration.policy_version,
        "quiet_hours_start": configuration.quiet_hours_start,
        "quiet_hours_end": configuration.quiet_hours_end,
        "contact_limit": configuration.contact_limit,
        "kill_switch": configuration.kill_switch,
        "mock_identity": configuration.mock_identity,
    }


def _time(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
