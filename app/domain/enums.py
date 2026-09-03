from enum import StrEnum


class PaymentEventType(StrEnum):
    FAILED = "payment.failed"
    CAPTURED = "payment.captured"
    SUBSCRIPTION_CHARGED = "subscription.charged"


class PaymentStatus(StrEnum):
    FAILED = "failed"
    CAPTURED = "captured"


class EvidenceSource(StrEnum):
    """Truth-preserving provenance for provider and demo evidence."""

    RAZORPAY_TEST = "razorpay_test"
    SIMULATED_MERCHANT = "simulated_merchant"
    SIMULATED_PROVIDER = "simulated_provider"
    SIMULATED_BANK_RAIL = "simulated_bank_rail"
    MOCK = "mock"


class ClaimTag(StrEnum):
    ESTIMATED = "ESTIMATED"
    SIMULATED = "SIMULATED"
    TEST_MODE = "TEST MODE"
    MOCK = "MOCK"


class IncidentState(StrEnum):
    DETECTED = "detected"
    INVESTIGATING = "investigating"
    ACTIONABLE = "actionable"
    RECOVERY_IN_PROGRESS = "recovery_in_progress"
    MONITORING = "monitoring"
    RESOLVED = "resolved"


class CaseState(StrEnum):
    DETECTED = "detected"
    INVESTIGATED = "investigated"
    ELIGIBLE = "eligible"
    ACTION_SELECTED = "action_selected"
    AWAITING_OUTCOME = "awaiting_outcome"
    RECOVERED = "recovered"
    ESCALATED = "escalated"
    STOPPED = "stopped"
