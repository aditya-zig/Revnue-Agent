# Model limits

`RecoveryModel` is a local logistic-regression implementation in
`app/recovery/scoring.py`. It trains on rows generated in the source code with
a fixed seed. It does not train on merchant data and does not call an LLM or a
remote model.

The model uses amount, tenure, successful payments, prior failures, and action.
It reports a customer-disjoint training and holdout split, calibration values,
top-k precision, and net-value numbers for generated rows. Those measurements
describe the generator and code path. They do not establish performance for a
merchant population.

The model ranks actions after `evaluate_policy` removes prohibited actions. A
structured external decision function is optional. If it is missing, malformed,
or selects a blocked action, the controller records fallback selection and uses
the highest-ranked permitted action. The model cannot grant an action that the
policy denied.

The model does not estimate causal effect. It lacks production outcomes,
feature monitoring, drift detection, demographic or regional fairness analysis,
confidence intervals, a retraining process, and human approval rules for price
or discount changes. Do not use its probabilities to make claims about real
customers or expected revenue.
