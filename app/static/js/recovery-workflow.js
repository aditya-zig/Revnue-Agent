export async function approveRecoveryAction(createDecision, caseId, action) {
  // A resumed provider failure is a new action attempt, so do not replay the
  // failed ActionEvent's key. The button is disabled while this attempt runs.
  const attemptId = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
  const idempotencyKey = `decision:${attemptId}`;
  await createDecision(caseId, { idempotency_key: idempotencyKey, selected_action: action });
  return createDecision(
    caseId,
    { idempotency_key: idempotencyKey, selected_action: action, approved: true },
    { headers: { "X-Reroute-Role": "business_owner" } },
  );
}
