const API_ROOT = "/api/v1";
const POLL_MS = 3500;
const VIEWS = new Set(["home", "payments", "incidents", "recoveries", "exceptions", "policy", "outcomes", "evaluation"]);

const shell = document.querySelector("[data-sentinel-shell]");
const screen = document.getElementById("screen");
const announcer = document.querySelector("[data-announcer]");
const toastStack = document.querySelector("[data-toast-stack]");
const incidentCount = document.querySelector("[data-incident-count]");
const liveLabel = document.querySelector("[data-live-label]");
const latestEvent = document.querySelector("[data-latest-event]");

const state = {
  dashboard: null,
  incidents: [],
  incidentDetails: new Map(),
  view: "home",
  selectedIncidentId: null,
  loading: true,
  polling: false,
  replay: { running: false, progress: 0, stage: "" },
  knownIncidents: new Set(),
  initialIncidentSnapshotTaken: false,
  interactionBusy: false,
};

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function titleCase(value) {
  return String(value ?? "")
    .replaceAll("_", " ")
    .replaceAll("-", " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function humanAction(value) {
  const labels = {
    payment_link: "Send payment link",
    contact: "Contact customer",
    retry: "Retry payment",
    promise: "Record promise to pay",
    escalate: "Escalate to human",
    wait_and_retry: "Wait and retry",
    request_alternate_method: "Request alternate method",
    send_hinglish_reminder: "Send reminder",
    record_promise_to_pay: "Record promise to pay",
    escalate_human: "Escalate to human",
    stop: "Stop recovery",
  };
  return labels[value] || titleCase(value);
}

function compactMoney(paise, { exact = false } = {}) {
  const value = Number(paise ?? 0);
  const rupees = Number.isFinite(value) ? value / 100 : 0;
  if (exact) {
    return new Intl.NumberFormat("en-IN", {
      style: "currency",
      currency: "INR",
      maximumFractionDigits: rupees % 1 === 0 ? 0 : 2,
    }).format(rupees).replace("₹", "₹");
  }
  const abs = Math.abs(rupees);
  const sign = rupees < 0 ? "-" : "";
  if (abs >= 10_000_000) return `${sign}₹${trimNumber(abs / 10_000_000)}Cr`;
  if (abs >= 100_000) return `${sign}₹${trimNumber(abs / 100_000)}L`;
  if (abs >= 1_000) return `${sign}₹${trimNumber(abs / 1_000)}K`;
  return `${sign}₹${new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 }).format(abs)}`;
}

function trimNumber(value) {
  if (value >= 100) return value.toFixed(0);
  if (value >= 10) return value.toFixed(1).replace(/\.0$/, "");
  return value.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
}

function percent(value, digits = 1) {
  let number = Number(value ?? 0);
  if (!Number.isFinite(number)) number = 0;
  if (number <= 1) number *= 100;
  return `${number.toFixed(digits)}%`;
}

function dateTime(value) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return new Intl.DateTimeFormat("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    day: "2-digit",
    month: "short",
    hour12: false,
  }).format(parsed);
}

function timeOnly(value) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return new Intl.DateTimeFormat("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(parsed);
}

function claimTag(label) {
  if (!label) return "";
  const normalized = String(label).toUpperCase();
  const className = normalized.includes("TEST") ? "test" : normalized.includes("ESTIMATED") ? "estimated" : normalized.includes("MOCK") ? "mock" : "simulated";
  return `<span class="claim-tag ${className}">${esc(normalized)}</span>`;
}

function statusChip(label) {
  const normalized = String(label || "unknown").toLowerCase();
  let tone = "";
  if (["captured", "recovered", "resolved", "healthy", "completed"].includes(normalized)) tone = " style=\"color:var(--green);background:var(--green-soft)\"";
  else if (["failed", "blocked", "hard_decline"].includes(normalized)) tone = " style=\"color:var(--red);background:var(--red-soft)\"";
  else if (["detected", "investigating", "actionable", "eligible", "attention"].includes(normalized)) tone = " style=\"color:var(--amber);background:var(--amber-soft)\"";
  return `<span class="status-chip"${tone}>${esc(titleCase(label))}</span>`;
}

async function request(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("Accept", "application/json");
  let body = options.body;
  if (body != null && typeof body !== "string") {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(body);
  }
  const response = await fetch(`${API_ROOT}${path}`, { ...options, headers, body, credentials: "same-origin" });
  const type = response.headers.get("content-type") || "";
  const payload = type.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = payload && typeof payload === "object" ? payload.detail : payload;
    const error = new Error(Array.isArray(detail) ? detail.join(", ") : detail || `Request failed (${response.status})`);
    error.status = response.status;
    error.detail = detail;
    throw error;
  }
  return payload;
}

async function optionalRequest(path, options = {}) {
  try {
    return await request(path, options);
  } catch (error) {
    if ([404, 405].includes(error.status)) return null;
    throw error;
  }
}

function announce(message) {
  if (announcer) announcer.textContent = message;
}

function toast(title, message, { persistent = false } = {}) {
  if (!toastStack) return;
  const node = document.createElement("div");
  node.className = "toast";
  node.innerHTML = `<div><strong>${esc(title)}</strong><p>${esc(message)}</p></div><button type="button" aria-label="Dismiss notification">×</button>`;
  node.querySelector("button")?.addEventListener("click", () => node.remove());
  toastStack.append(node);
  if (!persistent) window.setTimeout(() => node.remove(), 7000);
}

function getHashState() {
  const raw = window.location.hash.replace(/^#/, "");
  if (raw.startsWith("incident/")) {
    return { view: "incidents", incidentId: decodeURIComponent(raw.slice("incident/".length)) };
  }
  return { view: VIEWS.has(raw) ? raw : "home", incidentId: null };
}

function syncNavigation() {
  document.querySelectorAll("[data-view]").forEach((button) => {
    const active = button.dataset.view === state.view;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
}

function navigate(view, incidentId = null) {
  state.view = VIEWS.has(view) ? view : "home";
  state.selectedIncidentId = incidentId;
  const hash = incidentId ? `incident/${encodeURIComponent(incidentId)}` : state.view;
  if (window.location.hash !== `#${hash}`) window.history.pushState(null, "", `#${hash}`);
  syncNavigation();
  render();
  document.getElementById("consoleMain")?.focus({ preventScroll: true });
  window.scrollTo({ top: 0, behavior: "smooth" });
  if (incidentId) void ensureIncidentDetail(incidentId);
}

function paymentSourceTag(payment) {
  const source = payment?.source_kind || payment?.provider;
  if (source === "razorpay_test") return claimTag("TEST MODE");
  if (source === "mock") return claimTag("MOCK");
  if (source?.startsWith?.("simulated") || source === "csv_import") return claimTag("SIMULATED");
  return "";
}

function recentPayments() {
  const explicit = state.dashboard?.recent_payments || [];
  if (explicit.length) return explicit;
  const rows = [];
  const seen = new Set();
  for (const item of state.dashboard?.worklist || []) {
    const evidence = item.evidence;
    if (!evidence) continue;
    const key = evidence.event_id || evidence.payment_id;
    if (seen.has(key)) continue;
    seen.add(key);
    rows.push(evidence);
  }
  const latest = state.dashboard?.population?.latest_test_mode_payment;
  if (latest) {
    const key = latest.event_id || latest.payment_id;
    if (!seen.has(key)) rows.unshift(latest);
  }
  return rows.sort((a, b) => String(b.occurred_at || "").localeCompare(String(a.occurred_at || ""))).slice(0, 16);
}

function successRate() {
  const pop = state.dashboard?.population || {};
  const total = Number(pop.total || 0);
  return total ? Number(pop.captured || 0) / total : 0;
}

function metricValue(obj, names, fallback = null) {
  for (const name of names) {
    const value = obj?.[name];
    if (value != null) return value;
  }
  return fallback;
}

function legacyIncident() {
  const finding = state.dashboard?.investigation;
  if (!finding) return null;
  const dimension = metricValue(finding, ["dimension"], "payment cohort");
  const value = metricValue(finding, ["value"], "degraded cohort");
  const baseline = metricValue(finding, ["baseline_success_rate", "baseline_rate", "overall_success_rate"], null);
  const observed = metricValue(finding, ["cohort_success_rate", "observed_success_rate", "success_rate"], null);
  return {
    incident_id: `finding:${finding.finding_id || "top"}`,
    state: "actionable",
    opened_at: finding.created_at || null,
    cohort_filter: { [dimension]: value },
    baseline_metrics: baseline == null ? {} : { success_rate: baseline },
    observed_metrics: observed == null ? {} : { success_rate: observed },
    affected_attempt_count: metricValue(finding, ["sample_size", "failed_count", "cohort_size"], state.dashboard?.population?.failed || 0),
    estimated_amount_at_risk: metricValue(finding, ["recoverable_impact", "impact"], state.dashboard?.executive?.estimated_value || 0),
    confidence: metricValue(finding, ["confidence", "confidence_score"], 0.82),
    provenance_summary: { source_kind: "simulated_merchant" },
    _legacyFinding: finding,
  };
}

function visibleIncidents() {
  if (state.incidents.length) return state.incidents;
  const fallback = legacyIncident();
  return fallback ? [fallback] : [];
}

function incidentName(incident) {
  const cohort = incident?.cohort_filter || {};
  const entries = Object.entries(cohort);
  if (entries.length) {
    const [key, value] = entries[0];
    return `${titleCase(key)} degradation · ${titleCase(value)}`;
  }
  const legacy = incident?._legacyFinding;
  if (legacy?.dimension && legacy?.value) return `${titleCase(legacy.dimension)} degradation · ${titleCase(legacy.value)}`;
  return "Payment success degradation";
}

function incidentRates(incident) {
  const baseline = metricValue(incident?.baseline_metrics, ["success_rate", "success_rate_pct", "rate"], null);
  const observed = metricValue(incident?.observed_metrics, ["success_rate", "success_rate_pct", "rate"], null);
  return { baseline, observed };
}

function linkedCaseForIncident(detail) {
  const chain = detail?.case_chain || detail?.linked_cases || [];
  const candidate = chain.find((item) => item.case_id) || chain[0];
  const caseId = candidate?.case_id || candidate;
  if (caseId) return state.dashboard?.worklist?.find((item) => item.case_id === caseId) || { case_id: caseId };
  const worklist = state.dashboard?.worklist || [];
  return worklist.find((item) => item.state === "eligible") || worklist.find((item) => item.state === "detected") || worklist[0] || null;
}

function outcomeForCase(caseId) {
  const timeline = state.dashboard?.timeline?.find((item) => item.case_id === caseId)?.events || [];
  return [...timeline].reverse().find((event) => event.kind === "outcome")?.data || null;
}

function latestActionForCase(caseId) {
  const timeline = state.dashboard?.timeline?.find((item) => item.case_id === caseId)?.events || [];
  return [...timeline].reverse().find((event) => event.kind === "action")?.data || null;
}

function incidentOutcome(detail) {
  for (const chainItem of detail?.case_chain || []) {
    const outcome = outcomeForCase(chainItem.case_id);
    if (outcome) return { ...outcome, case_id: chainItem.case_id };
  }
  const linked = linkedCaseForIncident(detail);
  const outcome = linked ? outcomeForCase(linked.case_id) : null;
  return outcome ? { ...outcome, case_id: linked.case_id } : null;
}

function currentIncident() {
  return visibleIncidents().find((item) => item.incident_id === state.selectedIncidentId) || null;
}

function renderPageHeading(eyebrow, title, copy, action = "") {
  return `<section class="page-heading"><div class="page-heading-copy"><p class="eyebrow">${esc(eyebrow)}</p><h1>${esc(title)}</h1><p class="lede">${esc(copy)}</p></div>${action}</section>`;
}

function renderHealthStrip() {
  const pop = state.dashboard?.population || {};
  const incidents = visibleIncidents();
  const active = incidents.find((item) => !["resolved", "closed"].includes(String(item.state).toLowerCase()));
  const current = successRate();
  const { baseline, observed } = active ? incidentRates(active) : { baseline: null, observed: null };
  const displayRate = observed != null ? observed : current;
  const baselineRate = baseline != null ? baseline : current;
  const latest = recentPayments()[0];
  return `<section class="health-strip" aria-label="Payment health">
    <div class="health-cell primary">
      <div class="health-icon ${active ? "attention" : "healthy"}">${active ? "!" : "✓"}</div>
      <div><p class="health-primary-title">${active ? "Payment incident needs attention" : "Payments are healthy"}</p><p class="health-primary-copy">${active ? `${esc(incidentName(active))} is outside its normal baseline.` : "ReRoute is watching payment traffic. No material incident is open."}</p></div>
    </div>
    <div class="health-cell"><span class="health-label">Current success</span><strong class="health-value">${percent(displayRate)}</strong></div>
    <div class="health-cell"><span class="health-label">Baseline</span><strong class="health-value">${percent(baselineRate)}</strong></div>
    <div class="health-cell"><span class="health-label">Latest event</span><strong class="health-value" style="font-size:15px">${latest ? timeOnly(latest.occurred_at) : "—"}</strong></div>
  </section>`;
}

function renderDemoBanner() {
  const hasPopulation = Number(state.dashboard?.population?.total || 0) > 0;
  const running = state.replay.running;
  const label = running ? state.replay.stage || "Replaying merchant day…" : hasPopulation ? "Replay merchant day again" : "Start interactive demo";
  return `<section class="card demo-banner">
    <div class="card-body">
      <div class="demo-copy">
        <h2>Watch Sentinel find the problem for you</h2>
        <p>Replay a deterministic merchant day. Normal traffic establishes the baseline; the injected failure pattern emerges inside the replay and ReRoute surfaces the actionable payment issue automatically.</p>
        <div class="demo-meta">${claimTag("SIMULATED DEMO DATA")}<span class="lede">No production money moves.</span></div>
        ${running ? `<div class="demo-progress" aria-label="Replay progress"><span style="--progress:${state.replay.progress}%"></span></div>` : ""}
      </div>
      <button class="button button-primary" type="button" data-start-demo ${running ? "disabled" : ""}>${esc(label)}</button>
    </div>
  </section>`;
}

function renderPaymentFeed(limit = 9) {
  const payments = recentPayments().slice(0, limit);
  if (!payments.length) return `<div class="feed-empty">No payment activity yet. Start the interactive demo to establish a merchant baseline.</div>`;
  return `<div class="payment-feed">${payments.map((payment) => {
    const status = String(payment.status || payment.event_type || "event").includes("fail") ? "failed" : String(payment.status || "captured");
    return `<div class="payment-row">
      <span class="payment-time">${esc(timeOnly(payment.occurred_at))}</span>
      <span class="payment-method">${esc(titleCase(payment.method || "payment"))} ${paymentSourceTag(payment)}</span>
      <span class="payment-status ${esc(status)}">${esc(titleCase(status))}</span>
      <span class="payment-amount">${esc(compactMoney(payment.amount, { exact: true }))}</span>
    </div>`;
  }).join("")}</div>`;
}

function sparklinePoints(activeIncident) {
  const current = successRate();
  const rates = activeIncident ? incidentRates(activeIncident) : { baseline: current, observed: current };
  const baseline = Number(rates.baseline ?? current) <= 1 ? Number(rates.baseline ?? current) * 100 : Number(rates.baseline ?? current);
  const observed = Number(rates.observed ?? current) <= 1 ? Number(rates.observed ?? current) * 100 : Number(rates.observed ?? current);
  const normal = Number.isFinite(baseline) && baseline > 0 ? baseline : 91.8;
  const tail = Number.isFinite(observed) && observed > 0 ? observed : normal;
  const values = activeIncident ? [normal - 1.2, normal + .3, normal - .5, normal + .6, normal - .1, normal - 1.4, (normal + tail) / 2, tail] : [normal - 1.4, normal - .2, normal + .5, normal - .7, normal + .4, normal - .2, normal + .3, normal];
  const min = Math.min(...values, 45);
  const max = 100;
  return values.map((value, index) => {
    const x = 10 + index * (280 / (values.length - 1));
    const y = 104 - ((value - min) / Math.max(1, max - min)) * 86;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
}

function renderHealthChart(activeIncident) {
  const rates = activeIncident ? incidentRates(activeIncident) : null;
  const baseline = rates?.baseline ?? successRate();
  const observed = rates?.observed ?? successRate();
  return `<div class="sparkline-wrap"><svg class="sparkline" viewBox="0 0 300 116" role="img" aria-label="Payment success rate trend">
      <line class="axis" x1="10" y1="104" x2="290" y2="104"></line>
      <line class="baseline" x1="10" y1="28" x2="290" y2="28"></line>
      <polyline class="${activeIncident ? "incident" : "current"}" points="${sparklinePoints(activeIncident)}"></polyline>
    </svg></div>
    <div class="chart-legend"><span class="legend-line baseline"></span> Baseline ${percent(baseline)} <span class="legend-line"></span> Current ${percent(observed)}</div>`;
}

function renderIncidentCard(incident) {
  const { baseline, observed } = incidentRates(incident);
  return `<article class="card incident-card" data-incident-card="${esc(incident.incident_id)}">
    <div class="card-head"><div><div class="incident-title-row"><span class="attention-dot"></span><h2 style="margin:0">${esc(incidentName(incident))}</h2>${statusChip(incident.state)}</div><p>Sentinel detected a material cohort-level change and quantified the merchant impact.</p></div>${claimTag("ESTIMATED")}</div>
    <div class="incident-metrics">
      <div class="incident-metric"><span>Baseline success</span><strong>${baseline == null ? "—" : percent(baseline)}</strong></div>
      <div class="incident-metric"><span>Current success</span><strong style="color:var(--red)">${observed == null ? "—" : percent(observed)}</strong></div>
      <div class="incident-metric"><span>Affected attempts</span><strong>${esc(incident.affected_attempt_count ?? "—")}</strong></div>
      <div class="incident-metric"><span>Revenue at risk</span><strong>${esc(compactMoney(incident.estimated_amount_at_risk))}</strong></div>
    </div>
    <p class="incident-summary">${esc(incidentSummary(incident))}</p>
    <div class="card-actions"><button class="button button-primary" type="button" data-open-incident="${esc(incident.incident_id)}">Review incident</button><span class="lede">Confidence ${percent(incident.confidence ?? .82, 0)}</span></div>
  </article>`;
}

function incidentSummary(incident) {
  const cohort = Object.entries(incident?.cohort_filter || {})[0];
  if (cohort) return `Failures are concentrated in the ${titleCase(cohort[0])} cohort “${titleCase(cohort[1])}”. ReRoute will separate deterministic facts from hypotheses before recommending any recovery.`;
  return "Payment success moved far enough from the expected baseline to require investigation. ReRoute keeps facts, AI hypotheses and Policy authority separate.";
}

function renderHome() {
  const incidents = visibleIncidents();
  const active = incidents.find((item) => !["resolved", "closed"].includes(String(item.state).toLowerCase()));
  return `${renderPageHeading("Merchant operations", active ? "A payment incident needs your attention" : "Payments are running normally", active ? "ReRoute found the change in the background. Review the evidence, Policy and recommendation without hunting through gateway logs." : "ReRoute watches payment health quietly and interrupts you only when a material, actionable problem appears.")}
    ${renderHealthStrip()}
    ${renderDemoBanner()}
    <section class="home-layout">
      <div class="stack">
        <article class="card">
          <div class="card-head"><div><h2>Live payment activity</h2><p>A quiet operational feed, not a wall of gateway logs.</p></div><span class="live-status"><span class="status-dot"></span>Live</span></div>
          <div class="card-body">${renderPaymentFeed()}</div>
        </article>
        <article class="card"><div class="card-head"><div><h2>Payment success</h2><p>Current merchant performance against the incident baseline.</p></div></div><div class="card-body">${renderHealthChart(active)}</div></article>
      </div>
      <div class="stack">${active ? renderIncidentCard(active) : `<article class="card card-pad"><p class="eyebrow">Attention queue</p><h2>Nothing needs you right now</h2><p class="lede">Sentinel is monitoring payment traffic. Start the interactive demo to watch an incident emerge from a normal merchant day.</p></article>`}</div>
    </section>`;
}

function renderPayments() {
  const payments = recentPayments();
  return `${renderPageHeading("Operate", "Payments", "Recent normalized payment activity. Provider provenance stays attached to the evidence rather than being implied by presentation.")}
    <article class="card table-card"><div class="card-head"><div><h2>Recent activity</h2><p>${payments.length} recent normalized events visible to the operator console.</p></div></div><div class="table-scroll"><table><thead><tr><th>Time</th><th>Payment</th><th>Method</th><th>Status</th><th>Amount</th><th>Evidence source</th></tr></thead><tbody>${payments.length ? payments.map((payment) => `<tr><td>${esc(dateTime(payment.occurred_at))}</td><td><strong>${esc(payment.payment_id || payment.event_id || "—")}</strong></td><td>${esc(titleCase(payment.method || "—"))}</td><td>${statusChip(payment.status || payment.event_type)}</td><td>${esc(compactMoney(payment.amount, { exact: true }))}</td><td>${paymentSourceTag(payment) || esc(titleCase(payment.provider || payment.source_kind || "unknown"))}</td></tr>`).join("") : `<tr><td colspan="6">No payment activity yet.</td></tr>`}</tbody></table></div></article>`;
}

function renderIncidents() {
  const incidents = visibleIncidents();
  return `${renderPageHeading("Operate", "Incidents", "Population-level payment degradations. Detection is deterministic; AI does not decide whether an incident exists.")}
    <div class="grid grid-3" style="margin-bottom:18px"><article class="card metric-card"><span>Open incidents</span><strong>${incidents.filter((item) => !["resolved", "closed"].includes(String(item.state).toLowerCase())).length}</strong><small>Needs investigation or monitoring</small></article><article class="card metric-card"><span>Estimated exposure</span><strong>${compactMoney(incidents.reduce((sum, item) => sum + Number(item.estimated_amount_at_risk || 0), 0))}</strong><small>ESTIMATED, not booked loss</small></article><article class="card metric-card"><span>Detector authority</span><strong style="font-size:18px">Deterministic</strong><small>Model output cannot open an incident</small></article></div>
    <article class="card table-card"><div class="table-scroll"><table><thead><tr><th>Incident</th><th>State</th><th>Cohort</th><th>Affected</th><th>At risk</th><th>Opened</th><th></th></tr></thead><tbody>${incidents.length ? incidents.map((incident) => `<tr><td><strong>${esc(incident.incident_id)}</strong></td><td>${statusChip(incident.state)}</td><td>${esc(incidentName(incident))}</td><td>${esc(incident.affected_attempt_count ?? "—")}</td><td>${esc(compactMoney(incident.estimated_amount_at_risk))} ${claimTag("ESTIMATED")}</td><td>${esc(dateTime(incident.opened_at))}</td><td><button class="row-button" type="button" data-open-incident="${esc(incident.incident_id)}">Review</button></td></tr>`).join("") : `<tr><td colspan="7">No incident has been detected.</td></tr>`}</tbody></table></div></article>`;
}

function renderRecoveries() {
  const rows = state.dashboard?.worklist || [];
  return `${renderPageHeading("Operate", "Recoveries", "Individual payment obligations eligible for bounded recovery. Policy is evaluated before any ranking or execution.")}
    <article class="card table-card"><div class="table-scroll"><table><thead><tr><th>Recovery case</th><th>State</th><th>Amount at risk</th><th>Failure evidence</th><th>Top permitted action</th><th>Owner</th></tr></thead><tbody>${rows.length ? rows.slice(0, 80).map((item) => `<tr><td><strong>${esc(item.case_id)}</strong></td><td>${statusChip(item.state)}</td><td>${esc(compactMoney(item.amount_at_risk, { exact: true }))}</td><td>${esc(titleCase(item.evidence?.error_reason || item.evidence?.error_code || "Recorded failure"))}</td><td>${esc(humanAction(item.ranked_actions?.[0]?.action || item.selected_action || "—"))}</td><td>${esc(titleCase(item.owner || "operations"))}</td></tr>`).join("") : `<tr><td colspan="6">No recovery cases yet.</td></tr>`}</tbody></table></div></article>`;
}

function renderExceptions() {
  const rows = state.dashboard?.payment_exceptions || [];
  return `${renderPageHeading("Control", "Exceptions", "Customer debit claims and ambiguous payment states stay outside automatic customer-directed recovery until resolved.")}
    <article class="card table-card"><div class="table-scroll"><table><thead><tr><th>Exception</th><th>Case</th><th>Kind</th><th>State</th><th>Resolution</th><th>Opened</th></tr></thead><tbody>${rows.length ? rows.map((item) => `<tr><td><strong>${esc(item.exception_id || "—")}</strong></td><td>${esc(item.case_id || "—")}</td><td>${esc(titleCase(item.kind || "payment exception"))}</td><td>${statusChip(item.state)}</td><td>${esc(titleCase(item.resolution || "Pending"))}</td><td>${esc(dateTime(item.opened_at))}</td></tr>`).join("") : `<tr><td colspan="6">No open payment exceptions.</td></tr>`}</tbody></table></div></article>`;
}

function renderPolicy() {
  const config = state.dashboard?.policy_settings || {};
  const hardDecline = (state.dashboard?.worklist || []).find((item) => item.blocked_reasons?.retry?.includes?.("hard_decline"));
  return `${renderPageHeading("Control", "Policy & Safety", "Deterministic Policy is the authority boundary. AI can explain or rank only actions that survive this gate.")}
    <div class="grid grid-2">
      <article class="card policy-panel"><div class="card-head"><div><h2>Current safety controls</h2><p>Versioned operator rules applied before model ranking.</p></div>${statusChip(config.kill_switch ? "blocked" : "healthy")}</div><div class="card-body"><ul class="fact-list"><li class="fact-row"><span>Policy version</span><strong>${esc(config.policy_version || "v1")}</strong></li><li class="fact-row"><span>Kill switch</span><strong>${config.kill_switch ? "ON — execution blocked" : "Off"}</strong></li><li class="fact-row"><span>Contact limit</span><strong>${esc(config.contact_limit ?? 3)} contacts</strong></li><li class="fact-row"><span>Quiet hours</span><strong>${esc(config.quiet_hours_start ?? "22")}:00–${esc(config.quiet_hours_end ?? "8")}:00 IST</strong></li></ul></div></article>
      <article class="card policy-panel"><div class="card-head"><div><h2>Safety proof</h2><p>A blocked action never enters the AI ranking set.</p></div></div><div class="card-body">${hardDecline ? `<div class="policy-box blocked"><h4>Retry blocked</h4><ul class="policy-list"><li><strong>${esc(hardDecline.case_id)}</strong><span class="reason">Hard decline evidence → retry removed before AI ranking.</span></li></ul></div><p class="policy-proof">This is an authority boundary, not a model preference. The model cannot restore Retry after Policy removes it.</p>` : `<p class="lede">A hard-decline proof case will appear here when the deterministic demo population contains one.</p>`}</div></article>
    </div>`;
}

function allOutcomes() {
  const outcomes = [];
  for (const timeline of state.dashboard?.timeline || []) {
    for (const event of timeline.events || []) {
      if (event.kind === "outcome") outcomes.push({ case_id: timeline.case_id, ...event.data, at: event.at });
    }
  }
  return outcomes;
}

function renderOutcomes() {
  const outcomes = allOutcomes();
  const testModeRecovered = outcomes.filter((item) => item.source === "razorpay_test" && item.recovered).reduce((sum, item) => sum + Number(item.recovered_amount || 0), 0);
  return `${renderPageHeading("Prove", "Outcomes", "Recovery claims appear only when persisted outcome evidence exists. Test Mode, simulated and mock sources remain visibly distinct.")}
    <div class="grid grid-3" style="margin-bottom:18px"><article class="card metric-card"><span>Razorpay Test Mode recovered</span><strong>${compactMoney(testModeRecovered)}</strong><small>Provider-backed persisted outcomes</small></article><article class="card metric-card"><span>Recorded outcomes</span><strong>${outcomes.length}</strong><small>Across all evidence sources</small></article><article class="card metric-card"><span>Claim boundary</span><strong style="font-size:18px">Evidence first</strong><small>No provider evidence → no recovered claim</small></article></div>
    <article class="card table-card"><div class="table-scroll"><table><thead><tr><th>Case</th><th>Result</th><th>Recovered</th><th>Source</th><th>Resolved</th></tr></thead><tbody>${outcomes.length ? outcomes.map((item) => `<tr><td><strong>${esc(item.case_id)}</strong></td><td>${statusChip(item.recovered ? "recovered" : "not recovered")}</td><td>${esc(compactMoney(item.recovered_amount, { exact: true }))}</td><td>${claimTag(item.source === "razorpay_test" ? "TEST MODE" : item.source === "mock" ? "MOCK" : "SIMULATED")}</td><td>${esc(dateTime(item.at))}</td></tr>`).join("") : `<tr><td colspan="5">No persisted outcomes yet.</td></tr>`}</tbody></table></div></article>`;
}

function renderEvaluation() {
  const evaluation = state.dashboard?.evaluation || {};
  const adaptive = evaluation.adaptive || evaluation.metrics?.adaptive || {};
  const fixed = evaluation.fixed || evaluation.baseline || evaluation.metrics?.fixed || {};
  const pairs = [
    ["Recovered amount", compactMoney(metricValue(adaptive, ["recovered_amount", "recovered"], 0)), compactMoney(metricValue(fixed, ["recovered_amount", "recovered"], 0))],
    ["Recovery rate", percent(metricValue(adaptive, ["recovery_rate"], 0)), percent(metricValue(fixed, ["recovery_rate"], 0))],
    ["Policy violations", String(metricValue(adaptive, ["policy_violations", "violations"], 0)), String(metricValue(fixed, ["policy_violations", "violations"], 0))],
  ];
  return `${renderPageHeading("Prove", "Evaluation", "Reproducible benchmark results for the deterministic sandbox. These figures are simulated evaluation evidence, not production merchant performance.", claimTag("SIMULATED"))}
    <article class="card"><div class="card-head"><div><h2>Adaptive recovery vs fixed schedule</h2><p>Same deterministic evaluation population; different recovery strategy.</p></div>${claimTag("SIMULATED")}</div><div class="card-body"><div class="table-scroll"><table><thead><tr><th>Metric</th><th>ReRoute adaptive</th><th>Fixed Day 0/1/3</th></tr></thead><tbody>${pairs.map(([label, a, b]) => `<tr><td><strong>${esc(label)}</strong></td><td>${esc(a)}</td><td>${esc(b)}</td></tr>`).join("")}</tbody></table></div><p class="policy-proof">Evaluation is a deterministic sandbox comparison. It does not claim these recovery rates or amounts occurred in production.</p></div></article>`;
}

function renderIncidentDetail(incident, detail) {
  const pending = !detail && !incident?._legacyFinding;
  if (pending) return `${renderPageHeading("Incident", "Loading incident…", "Retrieving deterministic evidence and linked recovery state.")}<div class="card loading-card"></div>`;
  const resolvedDetail = detail || incident;
  const evidence = resolvedDetail?.evidence_bundle || {};
  const facts = evidence.observed_facts || [];
  const hypotheses = evidence.model_hypotheses || resolvedDetail?.analysis?.hypotheses || resolvedDetail?._legacyFinding?.analysis?.result?.hypotheses || [];
  const analysis = resolvedDetail?.analysis || resolvedDetail?.incident_analysis || resolvedDetail?._legacyFinding?.analysis || null;
  const linkedCase = linkedCaseForIncident(resolvedDetail);
  const policy = linkedCase?.policy || {};
  const ranked = linkedCase?.ranked_actions || resolvedDetail?.recommendation?.ranked_actions || [];
  const recommendation = ranked[0] || (resolvedDetail?.recommendation?.action ? resolvedDetail.recommendation : null);
  const outcome = incidentOutcome(resolvedDetail);
  const rates = incidentRates(resolvedDetail);
  const allowed = policy.allowed_actions || [];
  const blocked = policy.blocked_reasons || linkedCase?.blocked_reasons || {};
  const audit = resolvedDetail?.audit || [];
  const confidence = Number(resolvedDetail?.confidence ?? .82);
  return `<div class="incident-page">
    <section class="card incident-hero"><button class="back-link" type="button" data-back-incidents>← Back to incidents</button><div class="incident-hero-top"><div><p class="eyebrow">Payment incident · ${esc(resolvedDetail?.incident_id || incident?.incident_id)}</p><h1>${esc(incidentName(resolvedDetail || incident))}</h1><p class="incident-context">${esc(incidentSummary(resolvedDetail || incident))}</p></div><div>${statusChip(resolvedDetail?.state || incident?.state)} ${claimTag("ESTIMATED")}</div></div><div class="grid grid-4" style="margin-top:20px"><article class="card metric-card"><span>Baseline success</span><strong>${rates.baseline == null ? "—" : percent(rates.baseline)}</strong></article><article class="card metric-card"><span>Current success</span><strong style="color:var(--red)">${rates.observed == null ? "—" : percent(rates.observed)}</strong></article><article class="card metric-card"><span>Affected attempts</span><strong>${esc(resolvedDetail?.affected_attempt_count ?? "—")}</strong></article><article class="card metric-card"><span>Estimated at risk</span><strong>${compactMoney(resolvedDetail?.estimated_amount_at_risk)}</strong><small>${compactMoney(resolvedDetail?.estimated_amount_at_risk, { exact: true })}</small></article></div></section>

    ${outcome ? renderRecoveredOutcome(outcome, resolvedDetail) : ""}

    <div class="incident-detail-grid">
      <div class="stack">
        <article class="card"><div class="card-head"><div><p class="eyebrow">Deterministic evidence</p><h2>What ReRoute observed</h2><p>Facts below come from normalized payment evidence and detector calculations.</p></div></div><div class="card-body">${renderFacts(resolvedDetail, facts)}</div></article>
        ${renderAnalysisCard(resolvedDetail, analysis, hypotheses, confidence)}
        ${renderAuditCard(audit, linkedCase)}
      </div>
      <div class="stack">
        ${renderInvestigationProgress(Boolean(analysis || hypotheses.length), Boolean(Object.keys(policy).length), Boolean(recommendation), Boolean(outcome))}
        ${renderPolicyCard(allowed, blocked, policy.policy_version)}
        ${renderRecommendationCard(linkedCase, recommendation, outcome)}
      </div>
    </div>
  </div>`;
}

function renderFacts(detail, facts) {
  if (facts.length) {
    return `<ul class="fact-list">${facts.map((fact) => `<li class="fact-row"><span>${esc(fact.label || fact.name || titleCase(fact.key || "Fact"))}</span><strong>${esc(fact.value_paise != null ? compactMoney(fact.value_paise, { exact: true }) : fact.value ?? fact.observed ?? "—")}</strong></li>`).join("")}</ul>`;
  }
  const rates = incidentRates(detail);
  const provenance = detail?.provenance_summary || {};
  const cohort = Object.entries(detail?.cohort_filter || {}).map(([key, value]) => `${titleCase(key)} = ${titleCase(value)}`).join(", ") || "Affected payment cohort";
  return `<ul class="fact-list">
    <li class="fact-row"><span>Affected cohort</span><strong>${esc(cohort)}</strong></li>
    <li class="fact-row"><span>Baseline success</span><strong>${rates.baseline == null ? "—" : percent(rates.baseline)}</strong></li>
    <li class="fact-row"><span>Observed success</span><strong>${rates.observed == null ? "—" : percent(rates.observed)}</strong></li>
    <li class="fact-row"><span>Affected attempts</span><strong>${esc(detail?.affected_attempt_count ?? "—")}</strong></li>
    <li class="fact-row"><span>Revenue exposure</span><strong>${compactMoney(detail?.estimated_amount_at_risk, { exact: true })} · ESTIMATED</strong></li>
    <li class="fact-row"><span>Evidence provenance</span><strong>${esc(titleCase(provenance.source_kind || provenance.primary_source || "simulated merchant replay"))}</strong></li>
  </ul>`;
}

function renderAnalysisCard(detail, analysis, hypotheses, confidence) {
  const result = analysis?.result || analysis || {};
  const summary = result.summary || detail?.analysis_summary || (hypotheses.length ? "The observed pattern is consistent with a technical cohort-level degradation rather than a uniform customer-funds issue." : "AI analysis is not yet persisted for this incident. Deterministic evidence remains available and recovery safety does not depend on the model.");
  const uncertainty = result.uncertainty || result.uncertainty_statement || "This assessment is advisory. ReRoute does not treat a model hypothesis as provider fact.";
  const steps = result.next_validation_steps || result.validation_steps || [];
  const generated = result.external_model_generated ?? analysis?.external_model_generated ?? hypotheses.length > 0;
  return `<article class="card analysis-card"><div class="card-head"><div><span class="analysis-label">AI assessment · advisory only</span><h2 style="margin-top:6px">What may be causing it</h2><p>Sanitized aggregate evidence only. Policy remains deterministic.</p></div>${generated ? statusChip("analysis ready") : statusChip("fallback")}</div><div class="card-body"><p class="analysis-summary">${esc(summary)}</p><div class="confidence-line"><span>Incident confidence</span><div class="confidence-bar"><span style="width:${Math.max(0, Math.min(100, confidence <= 1 ? confidence * 100 : confidence))}%"></span></div><strong>${percent(confidence, 0)}</strong></div><div class="analysis-section"><h4>Hypotheses, not facts</h4>${hypotheses.length ? `<ul>${hypotheses.map((item) => `<li>${esc(typeof item === "string" ? item : item.text || item.hypothesis || JSON.stringify(item))}</li>`).join("")}</ul>` : `<p class="lede">No model hypothesis is required to enforce Policy or detect the incident.</p>`}</div>${steps.length ? `<div class="analysis-section"><h4>Validation steps</h4><ul>${steps.map((item) => `<li>${esc(item)}</li>`).join("")}</ul></div>` : ""}<p class="policy-proof"><strong>Uncertainty:</strong> ${esc(uncertainty)}</p></div></article>`;
}

function renderInvestigationProgress(hasAnalysis, hasPolicy, hasRecommendation, hasOutcome) {
  const steps = [["Evidence normalized", true], ["Incident detected", true], ["Sanitized analysis", hasAnalysis], ["Policy evaluated", hasPolicy], ["Safe actions ranked", hasRecommendation], ["Outcome verified", hasOutcome]];
  return `<article class="card"><div class="card-head"><div><h2>Investigation</h2><p>Automatic progress. No manual refresh required.</p></div></div><div class="card-body"><div class="progress-list">${steps.map(([label, done]) => `<div class="progress-step ${done ? "done" : ""}"><span class="progress-check">${done ? "✓" : ""}</span><span>${esc(label)}</span></div>`).join("")}</div></div></article>`;
}

function renderPolicyCard(allowed, blocked, version) {
  const blockedEntries = Object.entries(blocked || {});
  return `<article class="card policy-panel"><div class="card-head"><div><p class="eyebrow">Deterministic authority</p><h2>Policy</h2><p>Policy decides what may reach ranking. The model cannot override this block.</p></div><span class="truth-chip test">${esc(version || "POLICY v1")}</span></div><div class="card-body"><div class="policy-columns"><div class="policy-box allowed"><h4>Allowed</h4><ul class="policy-list">${allowed.length ? allowed.map((action) => `<li>${esc(humanAction(action))}</li>`).join("") : `<li>No action currently allowed</li>`}</ul></div><div class="policy-box blocked"><h4>Blocked</h4><ul class="policy-list">${blockedEntries.length ? blockedEntries.map(([action, reasons]) => `<li>${esc(humanAction(action))}<span class="reason">${esc((Array.isArray(reasons) ? reasons : [reasons]).map(titleCase).join(", "))}</span></li>`).join("") : `<li>No blocked actions in this case</li>`}</ul></div></div>${blockedEntries.some(([action, reasons]) => action === "retry" && (Array.isArray(reasons) ? reasons : [reasons]).includes("hard_decline")) ? `<p class="policy-proof"><strong>Retry removed before AI ranking.</strong> Hard-decline evidence makes this a deterministic safety decision, not a model preference.</p>` : `<p class="policy-proof">Only actions in the Allowed set may be shown to the ranking layer.</p>`}</div></article>`;
}

function renderRecommendationCard(linkedCase, recommendation, outcome) {
  if (outcome) return `<article class="card recommendation-card"><div class="card-head"><div><p class="eyebrow">Recommendation</p><h2>Recovery complete</h2><p>The approved action has a persisted outcome. No further execution is required.</p></div>${statusChip("recovered")}</div></article>`;
  if (!linkedCase) return `<article class="card recommendation-card"><div class="card-head"><div><p class="eyebrow">Recommendation</p><h2>No linked recovery case yet</h2><p>The incident can be investigated, but there is no individual obligation available for execution.</p></div></div></article>`;
  const top = recommendation || linkedCase.ranked_actions?.[0];
  if (!top) return `<article class="card recommendation-card"><div class="card-head"><div><p class="eyebrow">Recommendation</p><h2>Waiting for Policy-safe ranking</h2><p>ReRoute will not invent a recovery action before the case is eligible.</p></div></div><div class="card-actions">${linkedCase.state === "detected" ? `<button class="button button-primary" type="button" data-investigate-case="${esc(linkedCase.case_id)}">Run investigation</button>` : ""}</div></article>`;
  const action = top.action || top.selected_action;
  const probability = Number(top.recovery_probability ?? top.probability ?? 0);
  const expected = Number(top.expected_net_value ?? top.expected_value ?? linkedCase.expected_value ?? 0);
  const approvedOrWaiting = ["action_selected", "awaiting_outcome", "escalated"].includes(String(linkedCase.state));
  return `<article class="card recommendation-card"><div class="card-head"><div><p class="eyebrow">Ranked after Policy</p><h2>Recommended recovery</h2><p>Highest expected-value action from the deterministic Policy-permitted set.</p></div>${statusChip(linkedCase.state)}</div><div class="card-body"><div class="recommendation-top"><div><span class="recommendation-rank">Rank #1</span><div class="recommendation-action">${esc(humanAction(action))}</div><p class="lede">${esc(recommendationReason(action, linkedCase))}</p></div></div><div class="recommendation-metrics"><div class="recommendation-metric"><span>Estimated recovery probability</span><strong>${percent(probability)}</strong></div><div class="recommendation-metric"><span>Expected net value</span><strong>${compactMoney(expected)}</strong></div></div><div class="approval-box"><p><strong>Human approval required.</strong> ReRoute cannot approve itself. The approved action remains bounded by the same Policy at execution time.</p><button class="button button-primary" type="button" data-approve-case="${esc(linkedCase.case_id)}" data-approve-action="${esc(action)}" ${state.interactionBusy || approvedOrWaiting ? "disabled" : ""}>${state.interactionBusy ? "Approving…" : approvedOrWaiting ? "Approval recorded" : `Approve ${esc(humanAction(action))}`}</button></div></div>${latestActionForCase(linkedCase.case_id) ? `<div class="card-actions"><span class="lede">Latest action: ${esc(humanAction(latestActionForCase(linkedCase.case_id).tool))} · ${esc(titleCase(latestActionForCase(linkedCase.case_id).status))}</span></div>` : ""}</article>`;
}

function recommendationReason(action, linkedCase) {
  if (action === "payment_link") return "Give the customer a clean alternate path without retrying an unsafe instrument.";
  if (action === "retry") return "Retry is permitted because Policy found no hard-decline or contact-safety block.";
  if (action === "contact") return `Customer contact is permitted with ${linkedCase.contact_budget ?? "available"} contact budget remaining.`;
  return "This action has the highest expected value among the actions Policy currently permits.";
}

function renderRecoveredOutcome(outcome, detail) {
  const testMode = outcome.source === "razorpay_test";
  return `<section class="card outcome-hero"><p class="eyebrow">Verified outcome</p><div>${testMode ? claimTag("RAZORPAY TEST MODE") : claimTag(outcome.source === "mock" ? "MOCK" : "SIMULATED")}</div><div class="outcome-number">${esc(compactMoney(outcome.recovered_amount, { exact: true }))} RECOVERED</div><p class="outcome-copy">${testMode ? "Persisted provider evidence records the matching Test Mode payment outcome. This is not a production revenue claim." : "A persisted outcome exists, but its source is not genuine Razorpay Test Mode provider evidence."}</p><div class="evidence-chain"><span class="evidence-node">Incident ${esc(detail.incident_id || "")}</span><span class="evidence-arrow">→</span><span class="evidence-node">Case ${esc(outcome.case_id || "")}</span><span class="evidence-arrow">→</span><span class="evidence-node">Human approval</span><span class="evidence-arrow">→</span><span class="evidence-node">Provider action</span><span class="evidence-arrow">→</span><span class="evidence-node">Outcome</span></div></section>`;
}

function renderAuditCard(audit, linkedCase) {
  let events = audit;
  if (!events.length && linkedCase) {
    events = state.dashboard?.timeline?.find((item) => item.case_id === linkedCase.case_id)?.events?.map((item) => ({ event_type: item.kind === "audit" ? item.data?.type : item.kind, created_at: item.at })) || [];
  }
  return `<article class="card"><div class="card-head"><div><h2>Evidence & audit chain</h2><p>Readable operator trace. Raw payloads stay out of the primary path.</p></div></div><div class="card-body">${events.length ? `<ol class="audit-list">${events.slice(-18).map((event) => `<li class="audit-item"><span class="audit-dot"></span><span class="audit-time">${esc(timeOnly(event.created_at || event.at))}</span><span class="audit-event"><strong>${esc(titleCase(event.event_type || event.kind || "event"))}</strong></span></li>`).join("")}</ol>` : `<p class="lede">Audit evidence will appear as the incident progresses.</p>`}</div></article>`;
}

function render() {
  if (!screen) return;
  syncNavigation();
  if (state.loading && !state.dashboard) {
    screen.innerHTML = `${renderPageHeading("Merchant operations", "Loading payment operations…", "Connecting to normalized payment evidence, incident state and recovery controls.")}<div class="grid grid-3"><div class="card loading-card"></div><div class="card loading-card"></div><div class="card loading-card"></div></div>`;
    return;
  }
  if (state.selectedIncidentId) {
    const incident = currentIncident() || { incident_id: state.selectedIncidentId };
    screen.innerHTML = renderIncidentDetail(incident, state.incidentDetails.get(state.selectedIncidentId));
    bindScreenActions();
    return;
  }
  const renderers = {
    home: renderHome,
    payments: renderPayments,
    incidents: renderIncidents,
    recoveries: renderRecoveries,
    exceptions: renderExceptions,
    policy: renderPolicy,
    outcomes: renderOutcomes,
    evaluation: renderEvaluation,
  };
  screen.innerHTML = (renderers[state.view] || renderHome)();
  bindScreenActions();
}

function updateChrome() {
  const incidents = visibleIncidents();
  const active = incidents.filter((item) => !["resolved", "closed"].includes(String(item.state).toLowerCase()));
  if (incidentCount) {
    incidentCount.textContent = String(active.length);
    incidentCount.hidden = active.length === 0;
  }
  const payments = recentPayments();
  const latest = payments[0];
  if (latestEvent) latestEvent.textContent = latest ? `${titleCase(latest.method || "Payment")} ${titleCase(latest.status || latest.event_type || "event")} · ${compactMoney(latest.amount, { exact: true })}` : "Waiting for payment activity";
  if (liveLabel) liveLabel.textContent = active.length ? "Incident detected" : "Watching payments";
}

function processNewIncidents(nextIncidents) {
  const ids = nextIncidents.map((item) => item.incident_id);
  if (!state.initialIncidentSnapshotTaken) {
    state.knownIncidents = new Set(ids);
    state.initialIncidentSnapshotTaken = true;
    return;
  }
  for (const incident of nextIncidents) {
    if (!state.knownIncidents.has(incident.incident_id)) {
      state.knownIncidents.add(incident.incident_id);
      toast("Payment incident detected", `${incidentName(incident)} · ${compactMoney(incident.estimated_amount_at_risk)} estimated at risk.`, { persistent: true });
      announce(`Payment incident detected. ${incidentName(incident)}.`);
    }
  }
}

async function loadState({ quiet = false } = {}) {
  if (state.polling) return;
  state.polling = true;
  try {
    const [dashboard, incidents] = await Promise.all([
      request("/dashboard"),
      optionalRequest("/incidents"),
    ]);
    state.dashboard = dashboard;
    state.incidents = Array.isArray(incidents) ? incidents : [];
    processNewIncidents(state.incidents.length ? state.incidents : visibleIncidents());
    state.loading = false;
    shell?.setAttribute("data-state", "ready");
    updateChrome();
    render();
  } catch (error) {
    state.loading = false;
    shell?.setAttribute("data-state", "error");
    if (!quiet) toast("Could not load payment operations", error.message || "The operator console request failed.", { persistent: true });
    if (!state.dashboard && screen) screen.innerHTML = `${renderPageHeading("Connection issue", "Payment operations are unavailable", "ReRoute could not load its persisted operator state. No recovery action has been executed.")}<article class="card card-pad"><p class="lede">${esc(error.message || "Request failed")}</p><div style="margin-top:14px"><button class="button button-secondary" type="button" data-retry-load>Try again</button></div></article>`;
  } finally {
    state.polling = false;
  }
}

async function ensureIncidentDetail(incidentId) {
  if (incidentId.startsWith("finding:")) {
    const legacy = legacyIncident();
    if (legacy) {
      state.incidentDetails.set(incidentId, legacy);
      render();
    }
    return;
  }
  if (state.incidentDetails.has(incidentId)) return;
  try {
    const detail = await optionalRequest(`/incidents/${encodeURIComponent(incidentId)}`);
    if (detail) state.incidentDetails.set(incidentId, detail);
  } catch (error) {
    toast("Incident detail unavailable", error.message || "Could not retrieve incident evidence.");
  }
  render();
}

async function startInteractiveDemo(button) {
  if (state.replay.running) return;
  state.replay = { running: true, progress: 8, stage: "Preparing deterministic merchant day…" };
  if (button) button.disabled = true;
  render();
  const stages = [
    [22, "Establishing healthy payment baseline…"],
    [48, "Replaying normalized merchant payments…"],
    [72, "Comparing cohorts against baseline…"],
  ];
  let stageIndex = 0;
  const timer = window.setInterval(() => {
    if (!state.replay.running || stageIndex >= stages.length) return;
    const [progress, stage] = stages[stageIndex++];
    state.replay.progress = progress;
    state.replay.stage = stage;
    render();
  }, 650);
  try {
    const replay = await optionalRequest("/replay/start", { method: "POST" }) || await optionalRequest("/sandbox/replay", { method: "POST" }) || await request("/data/simulate-999", { method: "POST" });
    state.replay.progress = 90;
    state.replay.stage = "Detector evaluating payment cohorts…";
    render();
    await loadState({ quiet: true });
    await new Promise((resolve) => window.setTimeout(resolve, 550));
    state.replay.progress = 100;
    state.replay.stage = "Replay complete";
    await loadState({ quiet: true });
    const incident = visibleIncidents()[0];
    if (incident) {
      toast("ReRoute found a payment problem", `${incidentName(incident)} · ${compactMoney(incident.estimated_amount_at_risk)} estimated at risk.`, { persistent: true });
    } else {
      toast("Merchant replay complete", `${replay?.payments_total ?? replay?.payments_created ?? "Merchant"} payment events were processed. Sentinel is still monitoring for a material incident.`);
    }
  } catch (error) {
    toast("Interactive demo could not start", error.message || "The deterministic replay request failed.", { persistent: true });
  } finally {
    window.clearInterval(timer);
    window.setTimeout(() => {
      state.replay = { running: false, progress: 0, stage: "" };
      render();
    }, 700);
  }
}

async function investigateCase(caseId) {
  state.interactionBusy = true;
  render();
  try {
    await request(`/cases/${encodeURIComponent(caseId)}/investigate`, { method: "POST" });
    toast("Investigation complete", "Deterministic Policy and ranked permitted actions are ready for merchant review.");
    await loadState({ quiet: true });
    if (state.selectedIncidentId && !state.selectedIncidentId.startsWith("finding:")) {
      state.incidentDetails.delete(state.selectedIncidentId);
      await ensureIncidentDetail(state.selectedIncidentId);
    }
  } catch (error) {
    toast("Investigation failed", error.message || "The case could not be investigated.");
  } finally {
    state.interactionBusy = false;
    render();
  }
}

async function approveCase(caseId, action) {
  if (state.interactionBusy) return;
  state.interactionBusy = true;
  render();
  const attempt = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
  const key = `sentinel-approval:${attempt}`;
  try {
    await request(`/cases/${encodeURIComponent(caseId)}/decisions`, {
      method: "POST",
      body: { idempotency_key: key, selected_action: action },
    });
    await request(`/cases/${encodeURIComponent(caseId)}/decisions`, {
      method: "POST",
      headers: { "X-Reroute-Role": "business_owner" },
      body: { idempotency_key: key, selected_action: action, approved: true },
    });
    toast("Merchant approval recorded", `${humanAction(action)} was approved. ReRoute is now waiting for bounded execution/provider outcome evidence.`, { persistent: true });
    await loadState({ quiet: true });
    if (state.selectedIncidentId && !state.selectedIncidentId.startsWith("finding:")) {
      state.incidentDetails.delete(state.selectedIncidentId);
      await ensureIncidentDetail(state.selectedIncidentId);
    }
  } catch (error) {
    if (error.status === 502) {
      toast("Provider action needs attention", "Approval is recorded, but the Test Mode provider action did not complete. ReRoute has not claimed recovery.", { persistent: true });
      await loadState({ quiet: true });
    } else {
      toast("Approval was not executed", error.message || "The recovery decision failed. No outcome was invented.", { persistent: true });
    }
  } finally {
    state.interactionBusy = false;
    render();
  }
}

function bindScreenActions() {
  screen?.querySelector("[data-start-demo]")?.addEventListener("click", (event) => void startInteractiveDemo(event.currentTarget));
  screen?.querySelector("[data-retry-load]")?.addEventListener("click", () => void loadState());
  screen?.querySelectorAll("[data-open-incident]").forEach((button) => button.addEventListener("click", () => navigate("incidents", button.dataset.openIncident)));
  screen?.querySelector("[data-back-incidents]")?.addEventListener("click", () => navigate("incidents"));
  screen?.querySelectorAll("[data-investigate-case]").forEach((button) => button.addEventListener("click", () => void investigateCase(button.dataset.investigateCase)));
  screen?.querySelectorAll("[data-approve-case]").forEach((button) => button.addEventListener("click", () => void approveCase(button.dataset.approveCase, button.dataset.approveAction)));
}

document.querySelectorAll(".nav-item[data-view]").forEach((button) => {
  button.addEventListener("click", () => navigate(button.dataset.view));
});

window.addEventListener("hashchange", () => {
  const next = getHashState();
  state.view = next.view;
  state.selectedIncidentId = next.incidentId;
  syncNavigation();
  render();
  if (next.incidentId) void ensureIncidentDetail(next.incidentId);
});

const initial = getHashState();
state.view = initial.view;
state.selectedIncidentId = initial.incidentId;
syncNavigation();
render();
void loadState().then(() => {
  if (state.selectedIncidentId) void ensureIncidentDetail(state.selectedIncidentId);
  if (new URLSearchParams(window.location.search).get("autostart") === "1" && Number(state.dashboard?.population?.total || 0) === 0) {
    void startInteractiveDemo(null);
  }
});
window.setInterval(() => void loadState({ quiet: true }), POLL_MS);

window.ReRouteSentinel = {
  getState: () => ({
    view: state.view,
    selectedIncidentId: state.selectedIncidentId,
    incidentCount: visibleIncidents().length,
    replayRunning: state.replay.running,
    dashboard: state.dashboard,
  }),
  navigate,
  reload: () => loadState(),
};
