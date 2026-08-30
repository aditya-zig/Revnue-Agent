from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.tables import ActionEvent, Customer, PaymentEvent, PaymentException, RecoveryCase
from app.domain.models import PolicyResponse

POLICY_VERSION = "v1"
RECOVERY_ACTIONS = ["payment_link", "contact", "retry", "promise", "escalate"]
CONTACT_ACTIONS = ["contact", "promise"]
CUSTOMER_DIRECTED_ACTIONS = ["payment_link", *CONTACT_ACTIONS]
TERMINAL_CASE_STATES = {
    "paid",
    "refunded",
    "disputed",
    "opted_out",
    "closed",
    "recovered",
    "stopped",
    "escalated",
}
HARD_DECLINE_CODES = {
    "CARD_BLOCKED",
    "CARD_DECLINED",
    "CARD_EXPIRED",
    "CARD_NOT_SUPPORTED",
    "HARD_DECLINE",
}


def evaluate_policy(
    session: Session,
    case: RecoveryCase,
    now: datetime,
    quiet_hours_start: int,
    quiet_hours_end: int,
    kill_switch: bool = False,
    contact_limit: int = 3,
    policy_version: str = POLICY_VERSION,
) -> PolicyResponse:
    if kill_switch:
        return PolicyResponse(
            allowed_actions=[],
            blocked_reasons={action: ["kill_switch"] for action in RECOVERY_ACTIONS},
            policy_version=policy_version,
        )
    if case.state in TERMINAL_CASE_STATES:
        return PolicyResponse(
            allowed_actions=[],
            blocked_reasons={action: ["terminal_case"] for action in RECOVERY_ACTIONS},
            policy_version=policy_version,
        )
    blocked_reasons: dict[str, list[str]] = {}
    customer = session.get(Customer, case.customer_id) if case.customer_id else None
    if customer is None:
        for action in CUSTOMER_DIRECTED_ACTIONS:
            blocked_reasons[action] = ["missing_identity"]
    elif not customer.consent:
        for action in CUSTOMER_DIRECTED_ACTIONS:
            blocked_reasons[action] = ["missing_consent"]

    open_exception = session.scalar(
        select(PaymentException.exception_id).where(
            PaymentException.case_id == case.case_id,
            PaymentException.state == "open",
        )
    )
    if open_exception:
        for action in CUSTOMER_DIRECTED_ACTIONS:
            blocked_reasons.setdefault(action, []).append("payment_exception")

    contact_count = session.scalar(
        select(func.count()).select_from(ActionEvent).where(
            ActionEvent.case_id == case.case_id,
            ActionEvent.tool.in_(CONTACT_ACTIONS),
        )
    )
    if (contact_count or 0) >= contact_limit:
        for action in CONTACT_ACTIONS:
            blocked_reasons.setdefault(action, []).append("contact_limit")

    local_hour = now.astimezone(ZoneInfo("Asia/Kolkata")).hour
    if _is_quiet_hour(local_hour, quiet_hours_start, quiet_hours_end):
        for action in CUSTOMER_DIRECTED_ACTIONS:
            blocked_reasons.setdefault(action, []).append("quiet_hours")

    if getattr(case, "obligation_reference", None):
        error_codes = session.scalars(
            select(PaymentEvent.error_code).where(
                PaymentEvent.obligation_reference == case.obligation_reference
            )
        )
    else:
        error_codes = session.scalars(
            select(PaymentEvent.error_code).where(PaymentEvent.payment_id == case.payment_id)
        )
    if any(error_code and error_code.upper() in HARD_DECLINE_CODES for error_code in error_codes):
        blocked_reasons["retry"] = ["hard_decline"]

    recent_action = session.scalar(
        select(ActionEvent.action_id).where(
            ActionEvent.case_id == case.case_id,
            ActionEvent.status != "failed",
            ActionEvent.executed_at >= now.astimezone(UTC) - timedelta(hours=24),
        )
    )
    if recent_action:
        for action in RECOVERY_ACTIONS:
            blocked_reasons.setdefault(action, []).append("action_limit")

    return PolicyResponse(
        allowed_actions=[action for action in RECOVERY_ACTIONS if action not in blocked_reasons],
        blocked_reasons=blocked_reasons,
        policy_version=policy_version,
    )


def _is_quiet_hour(hour: int, start: int, end: int) -> bool:
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end
