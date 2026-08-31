import assert from "node:assert/strict";
import test from "node:test";

import { approveRecoveryAction } from "../../app/static/js/recovery-workflow.js";

test("persists a pending decision before granting approval", async () => {
  const calls = [];
  const createDecision = async (caseId, payload) => {
    calls.push({ caseId, payload });
    return { action: payload.approved ? { status: "failed" } : null };
  };

  await approveRecoveryAction(createDecision, "case-1", "payment_link");

  assert.deepEqual(calls, [
    { caseId: "case-1", payload: { idempotency_key: "decision:case-1:payment_link" } },
    {
      caseId: "case-1",
      payload: { idempotency_key: "decision:case-1:payment_link", approved: true },
    },
  ]);
});
