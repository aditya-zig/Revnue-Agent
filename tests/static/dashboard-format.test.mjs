import assert from "node:assert/strict";
import test from "node:test";

import { claimTagForSource, claimTagForSources, findOutcomeForCase, formatMoney } from "../../app/static/js/dashboard-format.js";
import { renderOverview } from "../../app/static/js/dashboard-view.js";

test("formats paise as one INR display convention", () => {
  assert.equal(formatMoney(249900), "₹INR 2,499.00");
  assert.equal(formatMoney(1), "₹INR 0.01");
});

test("keeps outcome source claims distinct", () => {
  assert.equal(claimTagForSource("mock"), "MOCK");
  assert.equal(claimTagForSource("razorpay_test"), "TEST MODE");
  assert.equal(claimTagForSource("csv_import"), "");
});

test("suppresses claims for mixed, unknown, and missing evidence", () => {
  assert.equal(claimTagForSources(["mock"]), "MOCK");
  assert.equal(claimTagForSources(["mock", "mock"]), "MOCK");
  assert.equal(claimTagForSources(["razorpay_test", "razorpay_test"]), "TEST MODE");
  assert.equal(claimTagForSources(["mock", "razorpay_test"]), "");
  assert.equal(claimTagForSources(["mock", "unknown"]), "");
  assert.equal(claimTagForSources([null]), "");
  assert.equal(claimTagForSources([]), "");
});

test("renders one estimated ClaimTag for the Overview policy signal amount", () => {
  const overview = renderOverview({
    worklist: [],
    timeline: [],
    investigation: {
      finding_id: "finding_1",
      recoverable_impact: 5000,
      confidence: 0.8,
    },
  });
  const policySignal = overview.split('<h2 class="panel-title">Policy signal</h2>')[1].split("</article>")[0];
  assert.equal((policySignal.match(/class="badge[^>]*">ESTIMATED<\/span>/g) || []).length, 1);
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
