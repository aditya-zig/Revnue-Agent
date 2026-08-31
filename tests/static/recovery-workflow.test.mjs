import assert from "node:assert/strict";
import test from "node:test";

import { approveRecoveryAction } from "../../app/static/js/recovery-workflow.js";

test("persists a pending decision before granting approval", async () => {
  const calls = [];
  const createDecision = async (caseId, payload, options) => {
    calls.push({ caseId, payload, options });
    return { action: payload.approved ? { status: "failed" } : null };
  };

  await approveRecoveryAction(createDecision, "case-1", "payment_link");

  assert.equal(calls.length, 2);
  assert.equal(calls[0].caseId, "case-1");
  assert.equal(calls[0].options, undefined);
  assert.equal(calls[1].caseId, "case-1");
  assert.deepEqual(calls[1].options, { headers: { "X-Reroute-Role": "business_owner" } });
  assert.equal(calls[0].payload.selected_action, "payment_link");
  assert.equal(calls[1].payload.selected_action, "payment_link");
  assert.equal(calls[0].payload.approved, undefined);
  assert.equal(calls[1].payload.approved, true);
  assert.equal(calls[0].payload.idempotency_key, calls[1].payload.idempotency_key);
  assert.ok(calls[0].payload.idempotency_key.length <= 128);
});
