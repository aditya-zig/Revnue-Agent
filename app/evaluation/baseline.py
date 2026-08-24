from dataclasses import dataclass
from random import Random

CASE_COUNT = 20
CONTACT_DAYS = (0, 1, 3)
CONTACT_COST_PAISE = 100
RETRY_COST_PAISE = 250


@dataclass(frozen=True)
class RecoveryCase:
    customer_id: str
    payment_event_id: str
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
        for _day in CONTACT_DAYS:
            contact_cost += CONTACT_COST_PAISE
            retry_cost += RETRY_COST_PAISE
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
            customer_id=f"customer_{index:03}",
            payment_event_id=f"event_{index:03}",
            amount=random.choice([49_900, 99_900, 249_900]),
            response_probability=random.uniform(0.2, 0.7),
        )
        for index in range(CASE_COUNT)
    ]
