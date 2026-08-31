export async function approveRecoveryAction(createDecision, caseId, action) {
  const idempotencyKey = `decision:${caseId}:${action}`;
  await createDecision(caseId, { idempotency_key: idempotencyKey });
  return createDecision(caseId, { idempotency_key: idempotencyKey, approved: true });
}
