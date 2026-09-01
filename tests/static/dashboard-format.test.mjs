import assert from "node:assert/strict";
import test from "node:test";

import { claimTagForSource, findOutcomeForCase, formatMoney } from "../../app/static/js/dashboard-format.js";

test("formats paise as one INR display convention", () => {
  assert.equal(formatMoney(249900), "₹INR 2,499.00");
  assert.equal(formatMoney(1), "₹INR 0.01");
});

test("keeps outcome source claims distinct", () => {
  assert.equal(claimTagForSource("mock"), "MOCK");
  assert.equal(claimTagForSource("razorpay_test"), "TEST MODE");
});

test("finds a persisted outcome from the dashboard timeline", () => {
  const data = {
    timeline: [{
      case_id: "case_001",
      events: [{ kind: "outcome", data: { recovered_amount: 249900, source: "mock" } }],
    }],
  };

  assert.deepEqual(findOutcomeForCase(data, "case_001"), {
    recovered_amount: 249900,
    source: "mock",
  });
  assert.equal(findOutcomeForCase(data, "missing"), null);
});
