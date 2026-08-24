from dataclasses import dataclass
from random import Random

CASE_COUNT = 20
CONTACT_COST = 100
RETRY_COST = 250


@dataclass(frozen=True)
class RecoveryCase:
    amount: int
    response_probability: float


def run_baseline(seed: int) -> dict[str, int | float]:
    random = Random(seed)
    cases = _generate_cases(random)
    recovered_amount = 0
    contact_cost = 0
    retry_cost = 0
    recovered_cases = 0

    for case in cases:
        for _day in (0, 1, 3):
            contact_cost += CONTACT_COST
            retry_cost += RETRY_COST
            if random.random() < case.response_probability:
                recovered_amount += case.amount
                recovered_cases += 1
                break

    return {
        "seed": seed,
        "case_count": len(cases),
        "recovered_amount": recovered_amount,
        "contact_cost": contact_cost,
        "retry_cost": retry_cost,
        "recovery_rate": recovered_cases / len(cases),
    }


def _generate_cases(random: Random) -> list[RecoveryCase]:
    return [
        RecoveryCase(
            amount=random.choice([49_900, 99_900, 249_900]),
            response_probability=random.uniform(0.2, 0.7),
        )
        for _ in range(CASE_COUNT)
    ]
