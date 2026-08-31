export async function approveRecoveryAction(createDecision, caseId, action) {
  // A resumed provider failure is a new action attempt, so do not replay the
  // failed ActionEvent's key. The button is disabled while this attempt runs.
  const attemptId = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
  const idempotencyKey = `decision:${caseId}:${action}:${attemptId}`;
  await createDecision(caseId, { idempotency_key: idempotencyKey });
  return createDecision(
    caseId,
    { idempotency_key: idempotencyKey, approved: true },
    { headers: { "X-Reroute-Role": "business_owner" } },
  );
}
