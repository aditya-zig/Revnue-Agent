from dataclasses import dataclass
from random import Random

CASE_COUNT = 20
CONTACT_DAYS = (0, 1, 3)
CONTACT_COST_PAISE = 100
RETRY_COST_PAISE = 250
LEAK_SCENARIO_FAILURE_COUNT = 6
LEAK_SCENARIO_HEADER = (
    "event_id",
    "event_type",
    "payment_id",
    "customer_id",
    "amount",
    "currency",
    "method",
    "status",
    "error_code",
    "error_reason",
    "occurred_at",
    "successful_payments",
)


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


def generate_leak_scenario_csv(seed: int) -> str:
    random = Random(seed)
    rows: list[list[str]] = []

    for index in range(LEAK_SCENARIO_FAILURE_COUNT):
        rows.append(
            [
                f"scenario_{seed}_failure_{index:03}",
                "payment.failed",
                f"scenario_payment_{seed}_failure_{index:03}",
                f"scenario_customer_{seed}_failure_{index:03}",
                "249900",
                "INR",
                "upi",
                "failed",
                "BAD_REQUEST_ERROR",
                "insufficient funds",
                f"2026-08-24T04:{index:02}:00+00:00",
                str(random.randint(1, 6)),
            ]
        )

    for index in range(LEAK_SCENARIO_FAILURE_COUNT, CASE_COUNT):
        rows.append(
            [
                f"scenario_{seed}_captured_{index:03}",
                "payment.captured",
                f"scenario_payment_{seed}_captured_{index:03}",
                f"scenario_customer_{seed}_captured_{index:03}",
                str(random.choice([4_900, 9_900, 19_900])),
                "INR",
                "card",
                "captured",
                "",
                "",
                f"2026-08-23T12:{index:02}:00+00:00",
                str(random.randint(0, 3)),
            ]
        )

    return "\n".join(",".join(row) for row in [list(LEAK_SCENARIO_HEADER), *rows])


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
