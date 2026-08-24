from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from math import exp
from random import Random

from app.db.tables import Customer, RecoveryCase

MODEL_VERSION = "v1"
ACTION_COSTS = {
    "payment_link": 25,
    "contact": 100,
    "retry": 250,
    "promise": 100,
    "escalate": 0,
}
ACTIONS = tuple(ACTION_COSTS)


@dataclass(frozen=True)
class TrainingRow:
    customer_id: str
    case_id: str
    action: str
    amount: int
    tenure_days: int
    successful_payments: int
    prior_failures: int
    recovered: bool


class RecoveryModel:
    def __init__(self) -> None:
        rows = _generate_rows()
        train, calibration, holdout = _split_rows(rows)
        self.weights = _fit_logistic(train, _features)
        self.calibration_weights = _fit_calibration(calibration, self.weights)
        self.report = _report(train, calibration, holdout, self.weights, self.calibration_weights)

    def rank(
        self, case: RecoveryCase, customer: Customer | None, allowed_actions: list[str]
    ) -> list[dict[str, int | float | str]]:
        rows = [
            TrainingRow(
                customer_id=case.customer_id or "unknown",
                case_id=case.case_id,
                action=action,
                amount=case.amount_at_risk,
                tenure_days=customer.tenure_days if customer else 0,
                successful_payments=customer.successful_payments if customer else 0,
                prior_failures=customer.prior_failures if customer else 0,
                recovered=False,
            )
            for action in allowed_actions
        ]
        scores: list[dict[str, int | float | str]] = []
        for row in rows:
            probability = _calibrated_probability(row, self.weights, self.calibration_weights)
            cost = ACTION_COSTS[row.action]
            scores.append(
                {
                    "action": row.action,
                    "recovery_probability": round(probability, 4),
                    "cost": cost,
                    "expected_net_value": round(probability * row.amount) - cost,
                }
            )
        return sorted(scores, key=lambda score: int(score["expected_net_value"]), reverse=True)


def _generate_rows(seed: int = 7) -> list[TrainingRow]:
    random = Random(seed)
    rows: list[TrainingRow] = []
    for index in range(60):
        customer_id = f"training_customer_{index:03}"
        tenure_days = random.randint(0, 720)
        successful_payments = random.randint(0, 16)
        prior_failures = random.randint(0, 6)
        amount = random.choice([49_900, 99_900, 249_900])
        for action in ACTIONS:
            probability = _hidden_probability(
                action, amount, tenure_days, successful_payments, prior_failures
            )
            rows.append(
                TrainingRow(
                    customer_id=customer_id,
                    case_id=f"training_case_{index:03}",
                    action=action,
                    amount=amount,
                    tenure_days=tenure_days,
                    successful_payments=successful_payments,
                    prior_failures=prior_failures,
                    recovered=random.random() < probability,
                )
            )
    return rows


def _split_rows(
    rows: list[TrainingRow],
) -> tuple[list[TrainingRow], list[TrainingRow], list[TrainingRow]]:
    train: list[TrainingRow] = []
    calibration: list[TrainingRow] = []
    holdout: list[TrainingRow] = []
    for row in rows:
        bucket = int(sha256(row.customer_id.encode()).hexdigest()[:8], 16) % 10
        if bucket < 6:
            train.append(row)
        elif bucket < 8:
            calibration.append(row)
        else:
            holdout.append(row)
    return train, calibration, holdout


def _hidden_probability(
    action: str,
    amount: int,
    tenure_days: int,
    successful_payments: int,
    prior_failures: int,
) -> float:
    action_effect = {
        "payment_link": 1.3,
        "contact": 0.9,
        "promise": 0.75,
        "retry": 0.5,
        "escalate": -0.8,
    }[action]
    logit = (
        -1.4
        + action_effect
        + amount / 500_000
        + tenure_days / 2_000
        + successful_payments / 20
        - prior_failures / 10
    )
    return _sigmoid(logit)


def _features(row: TrainingRow) -> list[float]:
    return [
        1.0,
        row.amount / 250_000,
        row.tenure_days / 365,
        row.successful_payments / 12,
        row.prior_failures / 6,
        *[1.0 if row.action == action else 0.0 for action in ACTIONS],
    ]


def _fit_logistic(
    rows: list[TrainingRow], feature_fn: Callable[[TrainingRow], list[float]]
) -> list[float]:
    weights = [0.0] * len(feature_fn(rows[0]))
    for _ in range(500):
        gradient = [0.0] * len(weights)
        for row in rows:
            features = feature_fn(row)
            error = _sigmoid(_dot(weights, features)) - float(row.recovered)
            for index, value in enumerate(features):
                gradient[index] += error * value
        for index in range(len(weights)):
            weights[index] -= 0.08 * (gradient[index] / len(rows) + 0.001 * weights[index])
    return weights


def _fit_calibration(rows: list[TrainingRow], weights: list[float]) -> list[float]:
    calibration_weights = [0.0, 1.0]
    for _ in range(300):
        gradient = [0.0, 0.0]
        for row in rows:
            logit = _dot(weights, _features(row))
            error = _sigmoid(calibration_weights[0] + calibration_weights[1] * logit) - float(
                row.recovered
            )
            gradient[0] += error
            gradient[1] += error * logit
        calibration_weights[0] -= 0.05 * gradient[0] / len(rows)
        calibration_weights[1] -= 0.05 * gradient[1] / len(rows)
    return calibration_weights


def _report(
    train: list[TrainingRow],
    calibration: list[TrainingRow],
    holdout: list[TrainingRow],
    weights: list[float],
    calibration_weights: list[float],
) -> dict:
    probabilities = [_calibrated_probability(row, weights, calibration_weights) for row in holdout]
    observed = [float(row.recovered) for row in holdout]
    top_k = min(10, len(holdout))
    top_indices = sorted(
        range(len(holdout)), key=lambda index: probabilities[index], reverse=True
    )[:top_k]
    expected_net_value = sum(
        probability * row.amount - ACTION_COSTS[row.action]
        for row, probability in zip(holdout, probabilities, strict=True)
    )
    realized_net_value = sum(
        (row.amount if row.recovered else 0) - ACTION_COSTS[row.action] for row in holdout
    )
    train_customers = {row.customer_id for row in train}
    holdout_customers = {row.customer_id for row in holdout}
    return {
        "model_version": MODEL_VERSION,
        "training_rows": len(train),
        "calibration_rows": len(calibration),
        "holdout_rows": len(holdout),
        "train_customers": len(train_customers),
        "holdout_customers": len(holdout_customers),
        "customer_overlap": len(train_customers & holdout_customers),
        "calibration": {
            "brier_score": round(
                sum(
                    (probability - outcome) ** 2
                    for probability, outcome in zip(probabilities, observed, strict=True)
                )
                / len(holdout),
                4,
            ),
            "mean_predicted_probability": round(sum(probabilities) / len(probabilities), 4),
            "observed_recovery_rate": round(sum(observed) / len(observed), 4),
        },
        "top_k_precision": {
            "k": top_k,
            "precision": round(sum(observed[index] for index in top_indices) / top_k, 4),
        },
        "net_value": {
            "expected_net_value": round(expected_net_value),
            "realized_net_value": realized_net_value,
        },
    }


def _calibrated_probability(
    row: TrainingRow, weights: list[float], calibration_weights: list[float]
) -> float:
    logit = _dot(weights, _features(row))
    return _sigmoid(calibration_weights[0] + calibration_weights[1] * logit)


def _dot(left: list[float], right: list[float]) -> float:
    return sum(
        left_value * right_value
        for left_value, right_value in zip(left, right, strict=True)
    )


def _sigmoid(value: float) -> float:
    return 1 / (1 + exp(-value))
