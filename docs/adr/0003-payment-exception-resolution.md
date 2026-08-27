# PaymentException resolution

ReRoute opens a PaymentException for a Customer debit claim or provider reversal signal and blocks recovery until it resolves. Clear provider evidence may resolve the exception as no debit, reversed, captured, or refunded. The case returns to investigation after no debit or reversal, recovers after capture, and stops after refund. ReRoute does not promise a refund before evidence confirms one.
