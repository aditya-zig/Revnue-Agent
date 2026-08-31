from enum import StrEnum


class PaymentEventType(StrEnum):
    FAILED = "payment.failed"
    CAPTURED = "payment.captured"
    SUBSCRIPTION_CHARGED = "subscription.charged"


class PaymentStatus(StrEnum):
    FAILED = "failed"
    CAPTURED = "captured"


class ClaimTag(StrEnum):
    ESTIMATED = "ESTIMATED"
    SIMULATED = "SIMULATED"
    TEST_MODE = "TEST MODE"
    MOCK = "MOCK"


class CaseState(StrEnum):
    DETECTED = "detected"
    INVESTIGATED = "investigated"
    ELIGIBLE = "eligible"
    ACTION_SELECTED = "action_selected"
    AWAITING_OUTCOME = "awaiting_outcome"
    RECOVERED = "recovered"
    ESCALATED = "escalated"
    STOPPED = "stopped"
