# Evaluation

The published comparison is deterministic synthetic simulation. It creates 30
identical case sets with seeds `0` through `29`, each with 30 cases. Every
policy receives the same generated cases for each seed. The implementation is
in `app/evaluation/comparison.py`; frozen output is in
`app/evaluation/published_results.json`.

All money values below are paise. Divide by 100 for INR.

| Policy | Recovered amount | Recovery rate | Cost | Contacts | Safety violations |
| --- | ---: | ---: | ---: | ---: | ---: |
| Adaptive | 51,961,100 | 0.4322 | 53,250 | 450 | 0 |
| Rules based | 50,062,700 | 0.4144 | 73,500 | 720 | 0 |
| Fixed retry | 34,874,100 | 0.2878 | 225,000 | 0 | 120 |

The fixed policy always retries. The simulator counts a violation when it
retries a hard decline, contacts an opted-out customer, or does anything other
than escalate after a provider failure. The adaptive and rules-based policies
encode different action selection rules, not observed merchant behavior.

Run the comparison yourself after setup:

```sh
curl http://127.0.0.1:8000/api/v1/evaluations/reproducible
```

The tests also replay duplicate delivery, late success, opt-out, hard decline,
and provider failure. Their expected audit event sequences are frozen in
`app/evaluation/published_exceptions.json` and checked by
`tests/integration/test_evaluations.py`.

These results do not measure revenue lift, customer response, fairness, or
production reliability. They only show that the simulated policies differ on
the generated cases and that the tested exception paths leave audit records.
