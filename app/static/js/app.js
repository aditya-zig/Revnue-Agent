import { ApiError, createAction, createDecision, createFindingAnalysis, getDashboard, resumeCase } from "./api.js";
import { claimTagForSource, findOutcomeForCase, formatMoney } from "./dashboard-format.js";
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

function updateKpi(slot, value, note) {
  const card = kpis.querySelector(`[data-kpi-slot="${slot}"]`);
  if (!card) return;
  const valueNode = card.querySelector("[data-kpi-value]");
  const noteNode = card.querySelector("[data-kpi-note]");
  if (valueNode) valueNode.textContent = value;
  if (noteNode) noteNode.textContent = note;
}

function renderKpis(data) {
  const executive = data?.executive || {};
  updateKpi("revenue-at-risk", formatMoney(executive.revenue_at_risk), "Open RecoveryCases");
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

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

function money(value) {
  return formatMoney(value);
}

function tag(value, kind = "") {
  return `<span class="badge ${kind}">${esc(value)}</span>`;
}

function sourceTag(item) {
  const claim = claimTagForSource(item.evidence?.provider);
  return claim ? tag(claim) : "";
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

function eventTitle(event) {
  return ({ "raw event": "Provider event", decision: "Policy decision", action: "Recovery action", audit: "Audit record", outcome: "Recorded outcome" })[event.kind] || event.kind;
}

function eventSummary(event) {
  const data = event.data || {};
  if (event.kind === "raw event") return [data.event_id, data.event_type, data.status, data.error_reason].filter(Boolean).join(" · ");
  if (event.kind === "decision") return [data.selected_action, `Policy ${data.policy_version}`, `Model ${data.model_version}`].filter(Boolean).join(" · ");
  if (event.kind === "action") return [data.tool, data.status, data.provider_reference].filter(Boolean).join(" · ");
  if (event.kind === "outcome") return [data.recovered ? "Recovered" : "Not recovered", claimTagForSource(data.source), data.recovered_amount != null ? money(data.recovered_amount) : ""].filter(Boolean).join(" · ");
  return data.type || "Audit record";
}

function caseRows(data) {
  return data.worklist?.length ? `<div class="case-list">${data.worklist.slice(0, 4).map((item) => `<div class="case-row"><div><div class="case-title">${esc(item.case_id)}</div><div class="case-sub">${esc(item.evidence?.error_reason || item.evidence?.status || "No payment evidence")}</div></div><div class="money-claim"><div><div class="case-sub">At risk</div><strong>${money(item.amount_at_risk)}</strong></div>${sourceTag(item)}</div><div>${tag(item.state)}</div><button class="btn" data-view="detail" data-case="${esc(item.case_id)}">Review</button></div>`).join("")}</div>` : `<div class="state"><div class="state-inner"><h3>No recovery cases</h3><p>Import a PaymentEvent to create a case with recorded evidence.</p></div></div>`;
}

function mockOutcomeDetails(data, caseId) {
  const outcome = findOutcomeForCase(data, caseId);
  if (!outcome || outcome.source !== "mock") return "";
  return `<div class="mock-outcome" data-persisted-outcome="mock"><div class="mock-outcome-head"><strong>Outcome recorded</strong>${tag("MOCK")}</div><span>${outcome.recovered ? "Recovered" : "Not recovered"} · ${money(outcome.recovered_amount)}</span><button class="btn" data-view="detail" data-case="${esc(caseId)}">Review Outcome</button></div>`;
}

function renderViews(data) {
  const focus = data.worklist?.find((item) => item.case_id === state.selectedCase) || data.worklist?.[0];
  const trace = data.timeline?.find((item) => item.case_id === focus?.case_id)?.events || [];
  const overview = `<div class="story"><article class="risk"><div><p class="eyebrow">Money at risk</p><h2>${esc(focus?.case_id || "No case")}</h2><div class="amount">${money(focus?.amount_at_risk)}</div><p>${esc(focus?.evidence?.error_reason || "No provider event recorded")}</p></div><div>${tag(focus?.state || "No cases")} ${sourceTag(focus || {})}</div></article><article class="panel trace-summary"><h2 class="panel-title">Recorded execution trace</h2>${trace.length ? trace.slice(-4).map((event, index) => `<div class="trace-step"><span class="step-dot">${index + 1}</span><div><strong>${eventTitle(event)}</strong><small>${esc(eventSummary(event))}</small></div></div>`).join("") : '<div class="empty">No trace events recorded.</div>'}</article></div><div class="grid"><article class="panel"><div class="panel-head"><h2 class="panel-title">Recovery queue</h2><button class="btn" data-view="queue">View all</button></div>${caseRows(data)}</article><article class="panel"><div class="panel-head"><h2 class="panel-title">Policy signal</h2>${tag("ESTIMATED")}</div><div class="panel-body">${data.investigation ? `<h3>${esc(data.investigation.finding_id)}</h3><div class="metric-claim"><span>${money(data.investigation.recoverable_impact)} estimated recoverable impact at ${Math.round(data.investigation.confidence * 100)}% confidence.</span>${tag("ESTIMATED")}</div>` : '<div class="empty">No persisted LeakFinding.</div>'}</div></article></div>`;
  const queue = `<div class="panel"><div class="panel-head"><h2 class="panel-title">Recovery queue</h2><span class="table-status">${data.worklist.length} cases</span></div><div class="toolbar"><div class="field"><label for="queueSearch">Search recovery cases</label><input id="queueSearch" type="search" placeholder="Case, payment, state, or error"></div></div><div class="table-wrap"><table><thead><tr><th scope="col">Case</th><th scope="col">Evidence</th><th scope="col">Owner</th><th scope="col">Policy output</th><th scope="col">Review</th></tr></thead><tbody>${data.worklist.length ? data.worklist.map((item) => `<tr><td><strong>${esc(item.case_id)}</strong><br><span class="case-sub">${money(item.amount_at_risk)} at risk ${sourceTag(item)}</span></td><td>${esc(item.evidence?.event_type || "No payment event")}<br><span class="case-sub">${esc(item.evidence?.error_reason || item.evidence?.status || "No evidence")}</span></td><td>${esc(item.owner)}<br><span class="case-sub">${item.contact_budget} contacts left</span></td><td>${item.policy.allowed_actions.length ? item.policy.allowed_actions.map((action) => tag(action)).join(" ") : tag("Blocked", "warning")}</td><td>${item.human_review.can_execute ? item.human_review.allowed_actions.map((action) => `<button class="btn btn-primary review" data-case="${esc(item.case_id)}" data-action="${esc(action)}">Approve ${esc(action)}</button>`).join(" ") : ["escalated", "awaiting_outcome"].includes(item.state) ? `<button class="btn resume" data-case="${esc(item.case_id)}">Resume as business owner</button>` : '<span class="case-sub">No action permitted</span>'}</td></tr>`).join("") : '<tr><td colspan="5" class="empty">No recovery cases have been recorded.</td></tr>'}</tbody></table></div></div>`;
  const detail = trace.length ? `<div class="detail-grid"><article class="panel"><div class="panel-head"><h2 class="panel-title">${esc(focus.case_id)}</h2>${tag("RECORDED TRACE")}</div>${trace.map((event) => `<div class="event"><div class="event-time">${esc(event.at || "No timestamp")}</div><div><h4>${eventTitle(event)} ${tag(event.kind)}</h4><p class="case-sub">${esc(eventSummary(event))}</p><pre>${esc(JSON.stringify(event.data, null, 2))}</pre></div></div>`).join("")}</article><article class="panel"><div class="panel-head"><h2 class="panel-title">Policy and action</h2></div><div class="panel-body">${focus.policy.allowed_actions.length ? focus.policy.allowed_actions.map((action) => tag(action)).join(" ") : tag("Blocked", "warning")}</div></article></div>` : '<div class="state">No case trace is available.</div>';
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
    state.selectedCase = button.dataset.case || state.selectedCase;
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
  document.querySelectorAll(".review").forEach((button) => button.addEventListener("click", async () => {
    const caseButtons = [...document.querySelectorAll(".review")].filter((candidate) => candidate.dataset.case === button.dataset.case);
    caseButtons.forEach((candidate) => { candidate.disabled = true; });
    try {
      await approveRecoveryAction(createDecision, button.dataset.case, button.dataset.action);
      await loadDashboard();
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
    claimTagForSource(item.evidence?.provider),
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

function bindShell() {
  document.querySelectorAll("[data-dashboard-navigation] [data-view]").forEach((tab) => {
    tab.addEventListener("click", () => setView(tab.dataset.view));
  });
  document.querySelectorAll('[data-action="refresh-dashboard"], [data-action="retry-dashboard"]').forEach((button) => {
    button.addEventListener("click", loadDashboard);
  });
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
    api: { getDashboard },
  };
  loadDashboard();
}

initialize();
