from dataclasses import dataclass
from math import sqrt
from random import Random

from app.recovery.scoring import ACTION_COSTS

SEEDS = tuple(range(30))
CASES_PER_SEED = 30
POLICIES = ("adaptive", "rules_based", "fixed")


@dataclass(frozen=True)
class EvaluationCase:
    amount: int
    recovery_probability: float
    opted_out: bool
    hard_decline: bool
    provider_failure: bool


def run_comparison() -> dict:
    runs = [
        _run_policy(policy, seed, _cases(seed))
        for policy in POLICIES
        for seed in SEEDS
    ]
    return {
        "seeds": list(SEEDS),
        "cases_per_seed": CASES_PER_SEED,
        "policies": {policy: _summary(policy, runs) for policy in POLICIES},
    }


def _cases(seed: int) -> list[EvaluationCase]:
    random = Random(seed)
    return [
        EvaluationCase(
            amount=random.choice([49_900, 99_900, 249_900]),
            recovery_probability=random.uniform(0.2, 0.7),
            opted_out=index % 13 == 0,
            hard_decline=index % 11 == 0,
            provider_failure=index % 17 == 0,
        )
        for index in range(CASES_PER_SEED)
    ]


def _run_policy(policy: str, seed: int, cases: list[EvaluationCase]) -> dict:
    random = Random(seed + 10_000)
    recovered_amount = cost = contacts = violations = 0
    predictions: list[float] = []
    outcomes: list[int] = []
    for index, case in enumerate(cases):
        action = _action(policy, case, index)
        cost += ACTION_COSTS[action]
        contacts += action in {"contact", "promise"}
        violations += _is_violation(case, action)
        probability = _probability(case, action)
        recovered = random.random() < probability
        recovered_amount += case.amount if recovered else 0
        predictions.append(probability)
        outcomes.append(int(recovered))
    return {
        "policy": policy,
        "seed": seed,
        "recovered_amount": recovered_amount,
        "recovery_rate": sum(outcomes) / len(cases),
        "recovery_cost": cost,
        "contacts": contacts,
        "safety_violations": violations,
        "brier_score": sum(
            (prediction - outcome) ** 2
            for prediction, outcome in zip(predictions, outcomes, strict=True)
        )
        / len(cases),
    }


def _action(policy: str, case: EvaluationCase, index: int) -> str:
    if policy == "fixed":
        return "retry"
    if case.opted_out or case.provider_failure:
        return "escalate"
    if case.hard_decline:
        return "payment_link"
    if policy == "rules_based":
        return "contact"
    return ("payment_link", "contact", "promise")[index % 3]


def _is_violation(case: EvaluationCase, action: str) -> int:
    return int(
        (case.opted_out and action in {"contact", "promise"})
        or (case.hard_decline and action == "retry")
        or (case.provider_failure and action != "escalate")
    )


def _probability(case: EvaluationCase, action: str) -> float:
    if case.opted_out or case.provider_failure:
        return 0.0
    multiplier = {
        "payment_link": 1.15,
        "contact": 1.0,
        "retry": 0.75,
        "promise": 0.9,
        "escalate": 0.0,
    }[action]
    if case.hard_decline and action == "retry":
        return 0.0
    return min(case.recovery_probability * multiplier, 1.0)


def _summary(policy: str, runs: list[dict]) -> dict:
    policy_runs = [run for run in runs if run["policy"] == policy]
    recovered = [int(run["recovered_amount"]) for run in policy_runs]
    return {
        "seed_count": len(policy_runs),
        "recovered_amount": sum(recovered),
        "recovery_rate": round(
            sum(float(run["recovery_rate"]) for run in policy_runs) / len(policy_runs), 4
        ),
        "recovery_cost": sum(int(run["recovery_cost"]) for run in policy_runs),
        "recovery_amount_variation": round(_standard_deviation(recovered), 2),
        "calibration_brier_score": round(
            sum(float(run["brier_score"]) for run in policy_runs) / len(policy_runs), 4
        ),
        "contacts": sum(int(run["contacts"]) for run in policy_runs),
        "safety_violations": sum(int(run["safety_violations"]) for run in policy_runs),
    }


def _standard_deviation(values: list[int]) -> float:
    mean = sum(values) / len(values)
    return sqrt(sum((value - mean) ** 2 for value in values) / len(values))
