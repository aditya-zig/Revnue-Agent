"""Persistence invariants for deterministic Sentinel incidents.

PaymentIncident keeps the latest observation window in its existing Session 1
columns, while detector evidence retains the immutable trigger snapshot and
peak incident impact. This prevents a healthy recovery window from erasing the
facts that caused the incident to open.
"""

from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapper

from app.db.tables import PaymentIncident

DETECTOR_VERSION = "sentinel-incident-v1"
RESOLUTION_REASON = "signal_recovered_before_investigation"


def _int(value: object | None) -> int:
    return int(value) if isinstance(value, (int, float)) else 0


def _float(value: object | None) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def _trigger_snapshot(
    incident: PaymentIncident,
    *,
    baseline: dict[str, Any] | None = None,
    observed: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    risk: int | None = None,
) -> dict[str, object]:
    baseline = baseline or dict(incident.baseline_metrics or {})
    observed = observed or dict(incident.observed_metrics or {})
    evidence = evidence or dict(incident.detection_evidence_json or {})
    return {
        "baseline_success_rate": _float(baseline.get("success_rate")),
        "current_success_rate": _float(observed.get("success_rate")),
        "baseline_attempts": _int(baseline.get("attempts")),
        "current_attempts": _int(observed.get("attempts")),
        "failed_attempts": _int(observed.get("failed_attempts")),
        "success_rate_drop": _float(evidence.get("success_rate_drop")),
        "z_score": _float(evidence.get("z_score")),
        "confidence": _float(incident.confidence),
        "attempted_value_paise": _int(observed.get("attempted_value_paise")),
        "failed_value_paise": _int(observed.get("failed_value_paise")),
        "recoverable_failed_value_paise": _int(
            observed.get("recoverable_failed_value_paise")
        ),
        "estimated_amount_at_risk_paise": (
            _int(incident.estimated_amount_at_risk) if risk is None else risk
        ),
        "estimated_recoverable_paise": _int(
            evidence.get("estimated_recoverable_paise")
        ),
        "baseline_window": evidence.get("baseline_window"),
        "current_window": evidence.get("current_window"),
    }


def _augment_insert(incident: PaymentIncident) -> None:
    if incident.detection_version != DETECTOR_VERSION:
        return
    evidence = dict(incident.detection_evidence_json or {})
    evidence.setdefault("trigger_snapshot", _trigger_snapshot(incident, evidence=evidence))
    evidence["peak_estimated_amount_at_risk_paise"] = _int(
        incident.estimated_amount_at_risk
    )
    evidence["peak_estimated_recoverable_paise"] = _int(
        evidence.get("estimated_recoverable_paise")
    )
    evidence["peak_failed_value_paise"] = _int(
        (incident.observed_metrics or {}).get("failed_value_paise")
    )
    evidence["peak_failed_attempt_count"] = _int(
        (incident.observed_metrics or {}).get("failed_attempts")
    )
    evidence["peak_affected_attempt_count"] = _int(incident.affected_attempt_count)
    incident.detection_evidence_json = evidence


def _previous_value(history: Any, fallback: object) -> object:
    return history.deleted[0] if history.deleted else fallback


@event.listens_for(PaymentIncident, "before_insert")
def preserve_trigger_on_insert(
    _mapper: Mapper[PaymentIncident],
    _connection: Connection,
    incident: PaymentIncident,
) -> None:
    _augment_insert(incident)


@event.listens_for(PaymentIncident, "before_update")
def preserve_peak_on_update(
    _mapper: Mapper[PaymentIncident],
    _connection: Connection,
    incident: PaymentIncident,
) -> None:
    if incident.detection_version != DETECTOR_VERSION:
        return

    state = inspect(incident)
    risk_history = state.attrs.estimated_amount_at_risk.history
    confidence_history = state.attrs.confidence.history
    affected_history = state.attrs.affected_attempt_count.history
    evidence_history = state.attrs.detection_evidence_json.history
    baseline_history = state.attrs.baseline_metrics.history
    observed_history = state.attrs.observed_metrics.history

    previous_risk = _int(
        _previous_value(risk_history, incident.estimated_amount_at_risk)
    )
    current_risk = _int(incident.estimated_amount_at_risk)
    peak_risk = max(previous_risk, current_risk)
    incident.estimated_amount_at_risk = peak_risk

    previous_confidence = _float(_previous_value(confidence_history, incident.confidence))
    incident.confidence = max(previous_confidence, _float(incident.confidence))

    previous_affected = _int(
        _previous_value(affected_history, incident.affected_attempt_count)
    )
    incident.affected_attempt_count = max(
        previous_affected, _int(incident.affected_attempt_count)
    )

    previous_evidence_value = _previous_value(evidence_history, {})
    previous_evidence = (
        dict(previous_evidence_value)
        if isinstance(previous_evidence_value, dict)
        else {}
    )
    current_evidence = dict(incident.detection_evidence_json or {})

    previous_baseline_value = _previous_value(
        baseline_history, incident.baseline_metrics or {}
    )
    previous_observed_value = _previous_value(
        observed_history, incident.observed_metrics or {}
    )
    previous_baseline = (
        dict(previous_baseline_value)
        if isinstance(previous_baseline_value, dict)
        else {}
    )
    previous_observed = (
        dict(previous_observed_value)
        if isinstance(previous_observed_value, dict)
        else {}
    )

    trigger = previous_evidence.get("trigger_snapshot")
    if not isinstance(trigger, dict):
        trigger = _trigger_snapshot(
            incident,
            baseline=previous_baseline,
            observed=previous_observed,
            evidence=previous_evidence,
            risk=previous_risk,
        )
    current_evidence["trigger_snapshot"] = trigger
    current_evidence["peak_estimated_amount_at_risk_paise"] = peak_risk
    current_evidence["peak_estimated_recoverable_paise"] = max(
        _int(previous_evidence.get("peak_estimated_recoverable_paise")),
        _int(previous_evidence.get("estimated_recoverable_paise")),
        _int(current_evidence.get("estimated_recoverable_paise")),
    )
    current_evidence["peak_failed_value_paise"] = max(
        _int(previous_evidence.get("peak_failed_value_paise")),
        _int(previous_observed.get("failed_value_paise")),
        _int((incident.observed_metrics or {}).get("failed_value_paise")),
    )
    current_evidence["peak_failed_attempt_count"] = max(
        _int(previous_evidence.get("peak_failed_attempt_count")),
        _int(previous_observed.get("failed_attempts")),
        _int((incident.observed_metrics or {}).get("failed_attempts")),
    )
    current_evidence["peak_affected_attempt_count"] = max(
        _int(previous_evidence.get("peak_affected_attempt_count")),
        incident.affected_attempt_count,
    )
    if incident.state == "resolved":
        current_evidence["resolution_reason"] = RESOLUTION_REASON
    incident.detection_evidence_json = current_evidence
