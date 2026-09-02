import { claimTagForSource, claimTagForSources, findOutcomeForCase, formatMoney } from "./dashboard-format.js";

export function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

export function money(value) {
  return formatMoney(value);
}

export function tag(value, kind = "") {
  return `<span class="badge ${kind}">${esc(value)}</span>`;
}

export function sourceTag(item) {
  const claim = claimTagForSources(item.evidence_providers);
  return claim ? tag(claim) : "";
}

export function eventTitle(event) {
  return ({ "raw event": "Provider event", decision: "Policy decision", action: "Recovery action", audit: "Audit record", outcome: "Recorded outcome" })[event.kind] || event.kind;
}

export function eventSummary(event) {
  const data = event.data || {};
  if (event.kind === "raw event") return [data.event_id, data.event_type, data.status, data.error_reason].filter(Boolean).join(" · ");
  if (event.kind === "decision") return [data.selected_action, `Policy ${data.policy_version}`, `Model ${data.model_version}`].filter(Boolean).join(" · ");
  if (event.kind === "action") return [data.tool, data.status, data.provider_reference].filter(Boolean).join(" · ");
  if (event.kind === "outcome") return [data.recovered ? "Recovered" : "Not recovered", claimTagForSource(data.source), data.recovered_amount != null ? money(data.recovered_amount) : ""].filter(Boolean).join(" · ");
  return data.type || "Audit record";
}

function caseRows(data) {
  const worklist = data.worklist || [];
  return worklist.length ? `<div class="case-list">${worklist.slice(0, 4).map((item) => `<div class="case-row"><div><div class="case-title">${esc(item.case_id)}</div><div class="case-sub">${esc(item.evidence?.error_reason || item.evidence?.status || "No payment evidence")}</div></div><div class="money-claim"><div><div class="case-sub">At risk</div><strong>${money(item.amount_at_risk)}</strong></div>${sourceTag(item)}</div><div>${tag(item.state)}</div><button class="btn" data-view="detail" data-case="${esc(item.case_id)}">Review</button></div>`).join("")}</div>` : `<div class="state"><div class="state-inner"><h3>No recovery cases</h3><p>Import a PaymentEvent to create a case with recorded evidence.</p></div></div>`;
}

function renderDemoProgress(data) {
  const population = data.population || {};
  const worklist = data.worklist || [];

  const total = Number(population.total || 0);
  const latestTestMode = population.latest_test_mode_payment;

  const latestCase = latestTestMode
    ? worklist.find(
        (item) =>
          (
            latestTestMode.obligation_reference &&
            item.obligation_reference === latestTestMode.obligation_reference
          ) ||
          item.payment_id === latestTestMode.payment_id,
      )
    : null;

  const outcome = latestCase
    ? findOutcomeForCase(data, latestCase.case_id)
    : null;
  // A recovery capture is a second PaymentEvent for the original demo case.
  const demoPaymentNumber =
    latestCase?.state === "recovered" && outcome ? total - 1 : total;

  if (latestCase?.state === "recovered" && outcome) {
    const claim = claimTagForSource(outcome.source);

    return `<section class="demo-progress demo-progress-complete">
      <div>
        <p class="eyebrow">Demo journey</p>
        <h2>Payment #${esc(demoPaymentNumber)} recovered</h2>
        <p>
          ${money(outcome.recovered_amount)} has a persisted recovery Outcome
          for <strong>${esc(latestCase.case_id)}</strong>.
        </p>

        <div class="demo-progress-meta">
          ${claim ? tag(claim) : ""}
          ${tag("RECOVERED")}
        </div>
      </div>

      <button
        class="btn btn-primary"
        data-view="detail"
        data-case="${esc(latestCase.case_id)}"
      >Review Outcome</button>
    </section>`;
  }

  if (latestCase?.state === "awaiting_outcome") {
    return `<section class="demo-progress">
      <div>
        <p class="eyebrow">Demo journey</p>
        <h2>Recovery action recorded</h2>
        <p>
          Payment #${esc(total)} is awaiting provider outcome evidence.
        </p>

        <div class="demo-progress-meta">
          ${tag("TEST MODE")}
          ${tag("AWAITING OUTCOME")}
        </div>
      </div>

      <button
        class="btn btn-primary"
        data-view="detail"
        data-case="${esc(latestCase.case_id)}"
      >Review RecoveryCase</button>
    </section>`;
  }

  if (latestCase?.state === "eligible") {
    return `<section class="demo-progress">
      <div>
        <p class="eyebrow">Demo journey</p>
        <h2>Payment #${esc(total)} ready for recovery</h2>
        <p>
          Investigation is complete. Policy has produced the permitted
          action set and ReRoute has ranked those options.
        </p>

        <div class="demo-progress-meta">
          ${tag("TEST MODE")}
          ${tag("ELIGIBLE")}
        </div>
      </div>

      <button
        class="btn btn-primary"
        data-view="detail"
        data-case="${esc(latestCase.case_id)}"
      >Review Ranked Actions</button>
    </section>`;
  }

  if (latestTestMode && latestCase) {
    return `<section class="demo-progress demo-progress-complete">
      <div>
        <p class="eyebrow">Demo journey</p>
        <h2>Payment #${esc(total)} recorded</h2>
        <p>
          Razorpay Test Mode event persisted for
          <strong>${esc(latestTestMode.payment_id)}</strong>.
        </p>

        <div class="demo-progress-meta">
          ${tag("TEST MODE")}
          ${tag(latestCase.state || latestTestMode.status || "recorded")}
        </div>
      </div>

      <button
        class="btn btn-primary"
        data-view="detail"
        data-case="${esc(latestCase.case_id)}"
      >Review Payment #${esc(total)}</button>
    </section>`;
  }

  if (total === 999) {
    return `<section class="demo-progress">
      <div>
        <p class="eyebrow">Demo journey</p>
        <h2>999-payment history ready</h2>
        <p>
          The deterministic merchant history is loaded.
          The next Razorpay Test Mode payment becomes payment #1000.
        </p>

        <div class="demo-progress-meta">
          ${tag("SIMULATED")}
        </div>
      </div>

      <a class="btn btn-primary" href="/storefront">
        Open Storefront
      </a>
    </section>`;
  }

  return "";
}

function renderSafetyProof(worklist) {
  const hardDecline = worklist.find(
    (item) => item.payment_id === "demo_hard_decline",
  );

  if (!hardDecline) return "";

  const retryReasons =
    hardDecline.policy?.blocked_reasons?.retry || [];

  const retryBlocked =
    !hardDecline.policy?.allowed_actions?.includes("retry") &&
    retryReasons.includes("hard_decline");

  return `<article class="panel safety-proof">
    <div class="panel-head">
      <div>
        <p class="eyebrow">Safety proof</p>
        <h2 class="panel-title">Hard decline</h2>
      </div>

      ${retryBlocked ? tag("RETRY BLOCKED", "warning") : tag("CHECK POLICY")}
    </div>

    <div class="panel-body">
      <p>
        Policy evaluates the recorded hard-decline evidence before ranking.
      </p>

      <div class="safety-proof-row">
        <span>Retry</span>
        <strong>${retryBlocked ? "Blocked by Policy" : "Review required"}</strong>
      </div>

      ${
        retryReasons.length
          ? `<div class="case-sub">
              Reason: ${esc(retryReasons.join(", "))}
            </div>`
          : ""
      }

      <button
        class="btn"
        data-view="detail"
        data-case="${esc(hardDecline.case_id)}"
      >Review Safety Case</button>
    </div>
  </article>`;
}

export function renderOverview(data, selectedCase = null) {
  const population = data.population || {};
  const worklist = data.worklist || [];
  const focus = worklist.find((item) => item.case_id === selectedCase) || worklist[0];
  const trace = (data.timeline || []).find((item) => item.case_id === focus?.case_id)?.events || [];
  const failureRate = Number(population.failure_rate || 0) * 100;

  return `${renderDemoProgress(data)}
  <div class="population-strip">
    <div class="population-stat"><span>Total payments</span><strong>${esc(population.total ?? 0)}</strong><small>Recorded PaymentEvents</small></div>
    <div class="population-stat"><span>Captured</span><strong>${esc(population.captured ?? 0)}</strong><small>Successful attempts</small></div>
    <div class="population-stat"><span>Failed</span><strong>${esc(population.failed ?? 0)}</strong><small>Failed attempts</small></div>
    <div class="population-stat"><span>Failure rate</span><strong>${failureRate.toFixed(2)}%</strong><small>Current persisted population</small></div>
  </div>
  <div class="story"><article class="risk"><div><p class="eyebrow">Money at risk</p><h2>${esc(focus?.case_id || "No case")}</h2><div class="amount">${money(focus?.amount_at_risk)}</div><p>${esc(focus?.evidence?.error_reason || "No provider event recorded")}</p></div><div>${tag(focus?.state || "No cases")} ${sourceTag(focus || {})}</div></article><article class="panel trace-summary"><h2 class="panel-title">Recorded execution trace</h2>${trace.length ? trace.slice(-4).map((event, index) => `<div class="trace-step"><span class="step-dot">${index + 1}</span><div><strong>${eventTitle(event)}</strong><small>${esc(eventSummary(event))}</small></div></div>`).join("") : '<div class="empty">No trace events recorded.</div>'}</article></div><div class="grid"><article class="panel"><div class="panel-head"><h2 class="panel-title">Recovery queue</h2><button class="btn" data-view="queue">View all</button></div>${caseRows(data)}</article><article class="panel"><div class="panel-head"><h2 class="panel-title">Policy signal</h2></div><div class="panel-body">${data.investigation ? `<h3>${esc(data.investigation.finding_id)}</h3><div class="metric-claim"><span>${money(data.investigation.recoverable_impact)} estimated recoverable impact at ${Math.round(data.investigation.confidence * 100)}% confidence.</span>${tag("ESTIMATED")}</div>` : '<div class="empty">No persisted LeakFinding.</div>'}</div></article>${renderSafetyProof(worklist)}</div>`;
}
