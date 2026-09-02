import { ApiError, createAction, createDecision, createFindingAnalysis, getDashboard, investigateCase, resumeCase, simulate999Payments } from "./api.js";
import { claimTagForSource, claimTagForSources, findOutcomeForCase, formatMoney } from "./dashboard-format.js";
import { eventSummary, eventTitle, esc, money, renderOverview, sourceTag, tag } from "./dashboard-view.js";
import { nextFocusIndex } from "./focus-trap.js";
import { approveRecoveryAction } from "./recovery-workflow.js";

const VIEW_IDS = [
  "overview",
  "queue",
  "detail",
  "exceptions",
  "governance",
  "investigation",
  "evaluation",
  "inbox",
];
const THEME_STORAGE_KEY = "reroute-dashboard-theme";

const shell = document.querySelector("[data-dashboard-shell]");
const content = document.getElementById("app");
const loadingState = document.getElementById("dashboardLoading");
const errorState = document.getElementById("dashboardError");
const liveRegion = document.getElementById("liveRegion");
const kpis = document.getElementById("dashboardKpis");
const refreshButton = document.querySelector('[data-action="refresh-dashboard"]');
const simulateButton = document.querySelector('[data-action="simulate-999"]');
const themeButton = document.querySelector('[data-action="toggle-theme"]');

if (!shell || !content || !loadingState || !errorState || !liveRegion || !kpis) {
  throw new Error("ReRoute dashboard shell is missing a required mount point");
}

const state = {
  data: null,
  view: "overview",
  controller: null,
  requestNumber: 0,
  zoomed: null,
  zoomTrigger: null,
  backdrop: null,
  selectedCase: null,
};

function announce(message) {
  liveRegion.textContent = message;
}

function dispatch(name, detail = {}) {
  shell.dispatchEvent(new CustomEvent(name, { detail }));
}

function validView(view) {
  return VIEW_IDS.includes(view) ? view : "overview";
}

function setView(view, { focus = false, updateHash = true } = {}) {
  const nextView = validView(view);
  state.view = nextView;

  document.querySelectorAll("[data-dashboard-view]").forEach((panel) => {
    const active = panel.id === nextView;
    panel.classList.toggle("active", active);
    panel.hidden = !active;
    panel.setAttribute("aria-hidden", String(!active));
  });

  document.querySelectorAll("[data-dashboard-navigation] [data-view]").forEach((tab) => {
    const active = tab.dataset.view === nextView;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
    if (active && focus) tab.focus();
  });

  if (updateHash && window.location.hash !== `#${nextView}`) {
    window.history.replaceState(null, "", `#${nextView}`);
  }
  dispatch("dashboard:view-change", { view: nextView });
}

function setTheme(isDark, { persist = true } = {}) {
  document.body.classList.toggle("dark", isDark);
  themeButton.setAttribute("aria-pressed", String(isDark));
  themeButton.setAttribute(
    "aria-label",
    isDark ? "Switch to light theme" : "Switch to dark theme",
  );
  if (persist) {
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, isDark ? "dark" : "light");
    } catch {
      // Private browsing or blocked storage should not disable the theme toggle.
    }
  }
  dispatch("dashboard:theme-change", { theme: isDark ? "dark" : "light" });
}

function storedTheme() {
  try {
    return window.localStorage.getItem(THEME_STORAGE_KEY) === "dark";
  } catch {
    return false;
  }
}

function updateKpi(slot, value, note, claim = null) {
  const card = kpis.querySelector(`[data-kpi-slot="${slot}"]`);
  if (!card) return;
  const valueNode = card.querySelector("[data-kpi-value]");
  const noteNode = card.querySelector("[data-kpi-note]");
  const claimNode = card.querySelector("[data-kpi-claim]");
  if (valueNode) valueNode.textContent = value;
  if (noteNode) noteNode.textContent = note;
  if (claimNode) {
    claimNode.textContent = claim || "";
    claimNode.hidden = !claim;
  }
}

function renderKpis(data) {
  const executive = data?.executive || {};
  updateKpi(
    "revenue-at-risk",
    formatMoney(executive.revenue_at_risk),
    "Open RecoveryCases",
    executive.revenue_at_risk_claim_tag,
  );
  updateKpi(
    "estimated-recoverable",
    formatMoney(executive.estimated_value),
    "Single top persisted LeakFinding",
  );
  updateKpi(
    "actual-recovered",
    formatMoney(executive.test_mode_value),
    "Recorded Outcome amount in Test Mode",
  );
  updateKpi("open-cases", String(executive.open_cases ?? 0), "Needs review or Action");
}

function setRequestState(mode, message = "") {
  const hasData = state.data !== null;
  shell.dataset.shellState = mode;
  if (mode === "loading") {
    loadingState.hidden = hasData;
    errorState.hidden = true;
    content.hidden = hasData ? false : true;
  } else if (mode === "refreshing") {
    loadingState.hidden = true;
    errorState.hidden = true;
    content.hidden = false;
  } else if (mode === "error") {
    loadingState.hidden = true;
    errorState.hidden = false;
    content.hidden = !hasData;
  } else {
    loadingState.hidden = true;
    errorState.hidden = true;
    content.hidden = false;
  }
  kpis.setAttribute("aria-busy", String(mode === "loading" || mode === "refreshing"));
  if (message) announce(message);
}

function errorMessage(error) {
  if (error instanceof ApiError) return error.message;
  if (error instanceof TypeError && !navigator.onLine) {
    return "You appear to be offline. Reconnect and try again.";
  }
  return "The dashboard request failed. Try again.";
}

async function loadDashboard() {
  const requestNumber = ++state.requestNumber;
  state.controller?.abort();
  state.controller = new AbortController();
  const refreshing = state.data !== null;
  setRequestState(refreshing ? "refreshing" : "loading", refreshing ? "Refreshing recovery data…" : "Loading recovery operations…");
  if (refreshButton) {
    refreshButton.disabled = true;
    refreshButton.textContent = "Refreshing…";
  }

  try {
    const data = await getDashboard({ signal: state.controller.signal });
    if (requestNumber !== state.requestNumber) return;
    state.data = data;
    renderKpis(data);
    renderViews(data);
    setRequestState("ready", "Recovery data refreshed.");
    dispatch("dashboard:data-loaded", { data, view: state.view });
  } catch (error) {
    if (error?.name === "AbortError" || requestNumber !== state.requestNumber) return;
    const message = errorMessage(error);
    setRequestState("error", message);
    dispatch("dashboard:data-error", { error, message });
  } finally {
    if (requestNumber === state.requestNumber) {
      state.controller = null;
      if (refreshButton) {
        refreshButton.disabled = false;
        refreshButton.textContent = "Refresh data";
      }
    }
  }
}

function analysisDetails(analysis) {
  if (!analysis) return "";
  const result = analysis.result || {};
  const facts = (result.observed_facts || []).map((fact) => {
    const value = fact.value_paise != null ? money(fact.value_paise) : fact.value;
    const claim = fact.claim_tag ? ` ${tag(fact.claim_tag)}` : "";
    return `<li><strong>${esc(fact.label)}:</strong> ${esc(value)}${claim}</li>`;
  }).join("");
  const hypotheses = (result.hypotheses || []).map((item) => `<li>${esc(item)}</li>`).join("");
  const steps = (result.next_validation_steps || []).map((item) => `<li>${esc(item)}</li>`).join("");
  const source = result.external_model_generated ? "OpenRouter advisory result." : "Saved deterministic fallback.";
  return `<div class="analysis-result"><p><strong>${source}</strong> ${esc(result.summary)}</p><h4>Observed facts</h4><ul>${facts}</ul><h4>Hypotheses</h4><ul>${hypotheses}</ul><h4>Next validation steps</h4><ul>${steps}</ul><p class="case-sub">${esc(result.model_statement || "No external model generated this analysis.")}</p><p>${tag(analysis.claim_tag)}</p></div>`;
}


function mockOutcomeDetails(data, caseId) {
  const outcome = findOutcomeForCase(data, caseId);
  if (!outcome || outcome.source !== "mock") return "";
  return `<div class="mock-outcome" data-persisted-outcome="mock"><div class="mock-outcome-head"><strong>Outcome recorded</strong>${tag("MOCK")}</div><span>${outcome.recovered ? "Recovered" : "Not recovered"} · ${money(outcome.recovered_amount)}</span><button class="btn" data-view="detail" data-case="${esc(caseId)}">Review Outcome</button></div>`;
}

function rankedRecoveryOptions(item) {
  const ranked = item?.ranked_actions || [];

  if (!ranked.length) {
    return `<div class="empty">
      No ranked recovery options are available in the current case state.
    </div>`;
  }

  return `<div class="ranked-actions">
    ${ranked
      .map((option, index) => {
        const probability =
          Number(option.recovery_probability || 0) * 100;

        return `<article class="ranked-action ${index === 0 ? "recommended" : ""}">
          <div class="ranked-action-rank">#${index + 1}</div>

          <div class="ranked-action-main">
            <div class="ranked-action-title">
              <strong>${esc(option.action)}</strong>
              ${index === 0 ? tag("RECOMMENDED") : ""}
            </div>

            <div class="ranked-action-metrics">
              <span>
                Recovery probability
                <strong>${probability.toFixed(1)}%</strong>
              </span>

              <span>
                Expected net value
                <strong>${money(option.expected_net_value)}</strong>
              </span>

              <span>
                Action cost
                <strong>${money(option.cost)}</strong>
              </span>
            </div>
          </div>
        </article>`;
      })
      .join("")}
  </div>`;
}

function recoveryExecutionSummary(data, item) {
  if (!item) return "";

  const timeline =
    data.timeline?.find((entry) => entry.case_id === item.case_id)?.events || [];

  const outcome = findOutcomeForCase(data, item.case_id);

  const latestAction = [...timeline]
    .reverse()
    .find((event) => event.kind === "action")?.data;

  if (outcome) {
    const claim = claimTagForSource(outcome.source);

    return `<section class="execution-summary recovered">
      <div class="execution-summary-head">
        <div>
          <p class="eyebrow">Outcome</p>
          <h3>Recovery recorded</h3>
        </div>

        <div>
          ${claim ? tag(claim) : ""}
          ${tag(outcome.recovered ? "RECOVERED" : "NOT RECOVERED")}
        </div>
      </div>

      <div class="execution-metrics">
        <div>
          <span>Recovered amount</span>
          <strong>${money(outcome.recovered_amount)}</strong>
        </div>

        <div>
          <span>RecoveryCase</span>
          <strong>${esc(item.case_id)}</strong>
        </div>
      </div>
    </section>`;
  }

  if (latestAction) {
    return `<section class="execution-summary">
      <div class="execution-summary-head">
        <div>
          <p class="eyebrow">Action</p>
          <h3>Recovery action recorded</h3>
        </div>

        ${tag(item.state || latestAction.status)}
      </div>

      <div class="execution-metrics">
        <div>
          <span>Action</span>
          <strong>${esc(latestAction.tool)}</strong>
        </div>

        <div>
          <span>Status</span>
          <strong>${esc(latestAction.status)}</strong>
        </div>

        <div>
          <span>Provider reference</span>
          <strong>${esc(latestAction.provider_reference || "—")}</strong>
        </div>
      </div>

      ${
        item.state === "awaiting_outcome"
          ? `<p class="case-sub">
              Waiting for authoritative provider outcome evidence.
            </p>`
          : ""
      }
    </section>`;
  }

  if (item.state === "eligible") {
    return `<section class="execution-summary">
      <div class="execution-summary-head">
        <div>
          <p class="eyebrow">Human review</p>
          <h3>Approval required</h3>
        </div>

        ${tag("ELIGIBLE")}
      </div>

      <p class="case-sub">
        ReRoute has ranked Policy-permitted actions.
        A business owner must approve one before execution.
      </p>
    </section>`;
  }

  return "";
}

function recoveryCaseControls(item) {
  if (!item) return "";

  if (item.state === "detected") {
    return `<button
      class="btn btn-primary investigate-case"
      type="button"
      data-case="${esc(item.case_id)}"
    >Investigate</button>`;
  }

  if (item.human_review?.can_execute) {
    const ranked = item.ranked_actions || [];

    const actions = ranked.length
      ? ranked.map((option) => option.action)
      : item.human_review.allowed_actions;

    return actions
      .map(
        (action, index) => `<button
          class="btn ${index === 0 ? "btn-primary" : ""} review"
          type="button"
          data-case="${esc(item.case_id)}"
          data-action="${esc(action)}"
        >${index === 0 ? "Approve recommended: " : "Approve "}${esc(action)}</button>`,
      )
      .join(" ");
  }

  if (["escalated", "awaiting_outcome"].includes(item.state)) {
    return `<button
      class="btn resume"
      type="button"
      data-case="${esc(item.case_id)}"
    >Resume as business owner</button>`;
  }

  return '<span class="case-sub">No action permitted</span>';
}

function renderViews(data) {
  const focus = data.worklist?.find((item) => item.case_id === state.selectedCase) || data.worklist?.[0];
  const trace = data.timeline?.find((item) => item.case_id === focus?.case_id)?.events || [];
  const overview = renderOverview(data, state.selectedCase);
  const queue = `<div class="panel"><div class="panel-head"><h2 class="panel-title">Recovery queue</h2><span class="table-status">${data.worklist.length} cases</span></div><div class="toolbar"><div class="field"><label for="queueSearch">Search recovery cases</label><input id="queueSearch" type="search" placeholder="Case, payment, state, or error"></div></div><div class="table-wrap"><table><thead><tr><th scope="col">Case</th><th scope="col">Evidence</th><th scope="col">Owner</th><th scope="col">Policy output</th><th scope="col">Review</th></tr></thead><tbody>${data.worklist.length ? data.worklist.map((item) => `<tr><td><strong>${esc(item.case_id)}</strong><br><span class="case-sub">${money(item.amount_at_risk)} at risk ${sourceTag(item)}</span></td><td>${esc(item.evidence?.event_type || "No payment event")}<br><span class="case-sub">${esc(item.evidence?.error_reason || item.evidence?.status || "No evidence")}</span></td><td>${esc(item.owner)}<br><span class="case-sub">${item.contact_budget} contacts left</span></td><td>${item.policy.allowed_actions.length ? item.policy.allowed_actions.map((action) => tag(action)).join(" ") : tag("Blocked", "warning")}</td><td>${recoveryCaseControls(item)}</td></tr>`).join("") : '<tr><td colspan="5" class="empty">No recovery cases have been recorded.</td></tr>'}</tbody></table></div></div>`;
  const detail = trace.length ? `<div class="detail-grid"><article class="panel"><div class="panel-head"><h2 class="panel-title">${esc(focus.case_id)}</h2>${tag("RECORDED TRACE")}</div>${trace.map((event) => `<div class="event"><div class="event-time">${esc(event.at || "No timestamp")}</div><div><h4>${eventTitle(event)} ${tag(event.kind)}</h4><p class="case-sub">${esc(eventSummary(event))}</p><pre>${esc(JSON.stringify(event.data, null, 2))}</pre></div></div>`).join("")}</article><article class="panel">
  <div class="panel-head">
    <div>
      <p class="eyebrow">Deterministic Policy → ranking</p>
      <h2 class="panel-title">Recovery strategy</h2>
    </div>
  </div>

  <div class="panel-body">
    <div class="policy-boundary">
      <div>
        <strong>Policy permits</strong>
        <div class="policy-action-tags">
          ${
            focus.policy.allowed_actions.length
              ? focus.policy.allowed_actions
                  .map((action) => tag(action))
                  .join(" ")
              : tag("Blocked", "warning")
          }
        </div>
      </div>

      <p>
        Ranking only scores actions allowed by Policy.
        It cannot restore a blocked action.
      </p>
    </div>

    <h3>Ranked recovery options</h3>

    ${rankedRecoveryOptions(focus)}

    <div class="case-actions">
      ${recoveryCaseControls(focus)}
    </div>
  </div>
</article></div>${recoveryExecutionSummary(data, focus)}` : '<div class="state">No case trace is available.</div>';
  const exceptions = data.payment_exceptions?.length ? data.payment_exceptions.map((item) => `<article class="panel"><div class="panel-head"><h2 class="panel-title">${esc(item.kind)}</h2>${tag(item.state)}</div><div class="panel-body"><p>${esc(item.case_id)}</p><h4>Original evidence</h4><pre class="json">${esc(JSON.stringify(item.evidence, null, 2))}</pre>${item.resolution ? `<h4>Resolution</h4><pre class="json">${esc(JSON.stringify(item.resolution, null, 2))}</pre><h4>Resolution evidence</h4><pre class="json">${esc(JSON.stringify(item.resolution_evidence, null, 2))}</pre>` : '<p class="case-sub">Unresolved. Resolution evidence is required.</p>'}</div></article>`).join("") : '<div class="state">No PaymentExceptions have been recorded.</div>';
  const governance = `<article class="panel"><div class="panel-head"><h2 class="panel-title">Governance</h2>${tag(data.policy_settings.policy_version)}</div><div class="panel-body"><div class="notice"><strong>Owner-only controls</strong><p>Operations can inspect the active policy. A business owner is required to change it.</p></div><h3>Active policy</h3><pre class="json">${esc(JSON.stringify(data.policy_settings, null, 2))}</pre></div></article>`;
  const investigation = data.investigation ? `<article class="panel"><div class="panel-head"><h2 class="panel-title">${esc(data.investigation.finding_id)}</h2>${tag("ESTIMATED")}</div><div class="panel-body"><p>${money(data.investigation.recoverable_impact)} estimated recoverable impact at ${Math.round(data.investigation.confidence * 100)}% confidence.</p><h4>Cohort</h4><pre class="json">${esc(JSON.stringify(data.investigation.cohort_filter, null, 2))}</pre>${data.investigation.analysis ? analysisDetails(data.investigation.analysis) : `<button class="btn btn-primary explain-finding" type="button" data-finding="${esc(data.investigation.finding_id)}">Explain finding</button><p class="case-sub">Requests the OpenRouter free model; failures return a saved deterministic fallback.</p>`}</div></article>` : '<div class="state">No persisted LeakFinding is available.</div>';
  const evaluation = `<article class="panel"><div class="panel-head"><h2 class="panel-title">Published evaluation</h2>${tag("SIMULATED")}</div><div class="panel-body"><div class="notice"><strong>Simulation only</strong><p>These values do not measure merchant recovery or provider outcomes.</p></div><pre class="json">${esc(JSON.stringify(data.evaluation, null, 2))}</pre></div></article>`;
  const inbox = data.mock_inbox?.length ? `<article class="panel"><div class="panel-head"><h2 class="panel-title">Mock messages</h2><span class="table-status">${data.mock_inbox.length} records</span></div>${data.mock_inbox.map((item) => `<div class="activity-row"><div class="activity-main"><strong>${esc(item.tool)} · ${esc(item.case_id)}</strong><span>${esc(item.reply || "Awaiting reply")} · ${esc(item.provider_reference || "No provider reference")}</span>${mockOutcomeDetails(data, item.case_id)}</div>${item.provider_reference && !item.reply ? `<form class="reply-form" data-provider-reference="${esc(item.provider_reference)}"><label class="sr-only" for="reply-${esc(item.provider_reference)}">Reply</label><select id="reply-${esc(item.provider_reference)}"><option>pay</option><option>ignore</option><option>promise</option><option>help</option><option>opt_out</option></select><button class="btn" type="submit">Record reply</button></form>` : tag(item.reply ? "Recorded" : "No provider reference")}</div>`).join("")}</article>` : '<div class="state">No mock messages have been recorded.</div>';
  const contentByView = { overview, queue, detail, exceptions, governance, investigation, evaluation, inbox };
  Object.entries(contentByView).forEach(([view, html]) => { const slot = document.querySelector(`[data-component-slot="${view}"]`); if (slot) slot.innerHTML = html; });
  bindRenderedActions();
}

function bindRenderedActions() {
  document.querySelectorAll("#app [data-view]").forEach((button) => button.addEventListener("click", () => {
    const selectedCase = button.dataset.case;
    state.selectedCase = selectedCase || state.selectedCase;
    if (selectedCase && state.data) renderViews(state.data);
    setView(button.dataset.view);
  }));
  document.querySelectorAll(".explain-finding").forEach((button) => button.addEventListener("click", async () => {
    button.disabled = true;
    button.textContent = "Saving explanation…";
    try {
      await createFindingAnalysis(button.dataset.finding, { idempotency_key: `${button.dataset.finding}:analysis:${crypto.randomUUID()}` });
      await loadDashboard();
      announce("Finding explanation saved.");
    } catch (error) {
      button.disabled = false;
      button.textContent = "Explain finding";
      announce(error.message || "Finding explanation could not be saved.");
    }
  }));
  document.querySelectorAll(".investigate-case").forEach((button) =>
    button.addEventListener("click", async () => {
      const caseId = button.dataset.case;
      button.disabled = true;
      button.textContent = "Investigating…";

      try {
        state.selectedCase = caseId;

        const result = await investigateCase(caseId);

        await loadDashboard();
        setView("detail");

        announce(
          result.new_state === "eligible"
            ? "Investigation complete. Policy marked the case eligible for recovery."
            : `Investigation complete. Case state: ${result.new_state}.`,
        );
      } catch (error) {
        button.disabled = false;
        button.textContent = "Investigate";
        announce(error.message || "Case investigation failed.");
      }
    }),
  );
  document.querySelectorAll(".review").forEach((button) => button.addEventListener("click", async () => {
    const caseButtons = [...document.querySelectorAll(".review")].filter((candidate) => candidate.dataset.case === button.dataset.case);
    caseButtons.forEach((candidate) => { candidate.disabled = true; });
    try {
      const result = await approveRecoveryAction(
        createDecision,
        button.dataset.case,
        button.dataset.action,
      );

      state.selectedCase = button.dataset.case;

      await loadDashboard();
      setView("detail");

      const executed = result?.action;

      announce(
        executed
          ? `Approved ${button.dataset.action}. Recovery action recorded.`
          : `Approved ${button.dataset.action}.`,
      );
    } catch (error) {
      announce(error.message || "Action could not be recorded.");
      caseButtons.forEach((candidate) => { candidate.disabled = false; });
    }
  }));
  document.querySelectorAll(".resume").forEach((button) => button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await resumeCase(button.dataset.case, { idempotency_key: `resume:${button.dataset.case}` }, { headers: { "X-Reroute-Role": "business_owner" } });
      await loadDashboard();
    } catch (error) {
      announce(error.message || "Case could not be resumed.");
      button.disabled = false;
    }
  }));
  document.querySelectorAll(".reply-form").forEach((form) => form.addEventListener("submit", async (event) => { event.preventDefault(); try { const response = await fetch(`/api/v1/mock-inbox/${encodeURIComponent(form.dataset.providerReference)}/reply`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reply: form.querySelector("select").value }) }); if (!response.ok) throw new Error("Mock reply could not be recorded."); await loadDashboard(); } catch (error) { announce(error.message); } }));
  const search = document.getElementById("queueSearch"); if (search) search.addEventListener("input", () => { const query = search.value.toLowerCase(); document.querySelectorAll("#queue tbody tr").forEach((row) => { row.hidden = !row.textContent.toLowerCase().includes(query); }); });
}

function csvCell(value) {
  return `"${String(value ?? "").replace(/"/g, '""')}"`;
}

function exportWorklist() {
  const rows = state.data?.worklist || [];
  if (!state.data) {
    announce("Recovery data is still loading; nothing was exported.");
    return;
  }
  const header = [
    "RecoveryCase",
    "PaymentObligation",
    "Customer",
    "Amount at risk",
    "Amount claim tag",
    "State",
    "Owner",
    "Policy actions",
  ];
  const csvRows = rows.map((item) => [
    item.case_id,
    item.obligation_reference,
    item.customer_id,
    item.amount_at_risk,
    claimTagForSources(item.evidence_providers),
    item.state,
    item.owner,
    item.policy?.allowed_actions?.join("; "),
  ]);
  const csv = [header, ...csvRows].map((row) => row.map(csvCell).join(",")).join("\n");
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = "reroute-recovery-worklist.csv";
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  announce(`Exported ${rows.length} RecoveryCases to CSV.`);
  dispatch("dashboard:worklist-exported", { count: rows.length });
}

function focusableIn(element) {
  return element.querySelector("button, input, select, textarea, a, [tabindex]:not([tabindex='-1'])");
}

function dialogFocusables(dialog) {
  const descendants = [...dialog.querySelectorAll("button, input, select, textarea, a, [tabindex]:not([tabindex='-1'])")];
  return [dialog, ...descendants].filter((element, index, elements) => (
    element !== dialog || dialog.tabIndex >= 0
  ) && elements.indexOf(element) === index);
}

function closeZoom() {
  if (!state.zoomed) return;
  state.zoomed.classList.remove("zoomed");
  state.zoomed.setAttribute("role", "button");
  state.zoomed.removeAttribute("aria-modal");
  state.backdrop?.remove();
  state.zoomed = null;
  state.backdrop = null;
  state.zoomTrigger?.focus();
  state.zoomTrigger = null;
  dispatch("dashboard:zoom-close");
}

function openZoom(element, trigger = element) {
  if (state.zoomed === element) {
    closeZoom();
    return;
  }
  closeZoom();
  state.zoomTrigger = trigger;
  state.backdrop = document.createElement("div");
  state.backdrop.className = "backdrop";
  state.backdrop.addEventListener("click", closeZoom);
  document.body.appendChild(state.backdrop);
  element.classList.add("zoomed");
  element.setAttribute("role", "dialog");
  element.setAttribute("aria-modal", "true");
  state.zoomed = element;
  const focusTarget = focusableIn(element) || (element.tabIndex >= 0 ? element : null);
  focusTarget?.focus();
  dispatch("dashboard:zoom-open", { element });
}

function isInteractive(target) {
  return Boolean(target.closest("button, input, select, textarea, a, [contenteditable='true']"));
}

function handleZoomClick(event) {
  const zoomButton = event.target.closest(".zoom-btn");
  if (zoomButton) {
    const panel = zoomButton.closest(".zoomable");
    if (panel && shell.contains(panel)) openZoom(panel, zoomButton);
    return;
  }
  const panel = event.target.closest(".zoomable");
  if (!panel || !shell.contains(panel) || isInteractive(event.target)) return;
  openZoom(panel, panel);
}

function handleKeydown(event) {
  if (event.key === "Escape" && state.zoomed) {
    event.preventDefault();
    closeZoom();
    return;
  }

  if (event.key === "Tab" && state.zoomed) {
    const focusables = dialogFocusables(state.zoomed);
    if (!focusables.length) return;
    const currentIndex = focusables.indexOf(document.activeElement);
    const nextIndex = nextFocusIndex(currentIndex, focusables.length, event.shiftKey);
    event.preventDefault();
    focusables[nextIndex].focus();
    return;
  }

  const tab = event.target.closest("[role='tab']");
  if (tab && shell.contains(tab)) {
    const tabs = [...document.querySelectorAll("[data-dashboard-navigation] [role='tab']")];
    const currentIndex = tabs.indexOf(tab);
    let nextIndex = currentIndex;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") nextIndex = (currentIndex + 1) % tabs.length;
    if (event.key === "ArrowLeft" || event.key === "ArrowUp") nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = tabs.length - 1;
    if (nextIndex !== currentIndex) {
      event.preventDefault();
      setView(tabs[nextIndex].dataset.view, { focus: true });
    }
    return;
  }

  const panel = event.target.closest(".zoomable");
  if (
    panel && shell.contains(panel) && !isInteractive(event.target) &&
    (event.key === "Enter" || event.key === " ")
  ) {
    event.preventDefault();
    openZoom(panel, panel);
  }
}

async function simulateHistory() {
  if (!simulateButton) return;

  simulateButton.disabled = true;
  simulateButton.textContent = "Simulating…";
  announce("Generating deterministic 999-payment history.");

  try {
    const summary = await simulate999Payments();
    simulateButton.textContent = "999 Payments Loaded";
    announce(
      `${summary.payments_total} simulated payments loaded: ` +
      `${summary.successes} captured and ${summary.failures} failed.`,
    );
    await loadDashboard();
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) {
      simulateButton.textContent = "History Already Present";
      announce(error.message);
      await loadDashboard();
      return;
    }

    simulateButton.textContent = "Simulation Failed";
    announce(errorMessage(error));
    window.setTimeout(() => {
      simulateButton.disabled = false;
      simulateButton.textContent = "Simulate 999 Payments";
    }, 2500);
  }
}

function bindShell() {
  document.querySelectorAll("[data-dashboard-navigation] [data-view]").forEach((tab) => {
    tab.addEventListener("click", () => setView(tab.dataset.view));
  });
  document.querySelectorAll('[data-action="refresh-dashboard"], [data-action="retry-dashboard"]').forEach((button) => {
    button.addEventListener("click", loadDashboard);
  });
  if (simulateButton) {
    simulateButton.addEventListener("click", simulateHistory);
  }
  themeButton.addEventListener("click", () => {
    setTheme(!document.body.classList.contains("dark"));
  });
  document.querySelector('[data-action="export-worklist"]').addEventListener("click", exportWorklist);
  document.addEventListener("click", handleZoomClick);
  document.addEventListener("keydown", handleKeydown);
  window.addEventListener("hashchange", () => setView(window.location.hash.slice(1), { updateHash: false }));
}

function initialize() {
  setTheme(storedTheme(), { persist: false });
  setView(validView(window.location.hash.slice(1)), { updateHash: false });
  bindShell();
  window.ReRouteDashboard = {
    getData: () => state.data,
    getView: () => state.view,
    refresh: loadDashboard,
    setView,
    exportWorklist,
    api: { getDashboard, simulate999Payments },
  };
  loadDashboard();
}

initialize();
