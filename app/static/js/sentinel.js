import { getDashboard } from "./api.js";
import {
  approveIncident,
  executeIncident,
  getIncident,
  getIncidentControl,
  getReplayStatus,
  investigateIncident,
  listIncidents,
  runReplay,
} from "./sentinel-api.js";

const root = document.getElementById("sentinel-app");
const crumb = document.querySelector("[data-crumb]");
const toast = document.querySelector("[data-toast]");
const state = {
  view: "home",
  dashboard: null,
  incidents: [],
  replay: null,
  selectedIncidentId: null,
  incident: null,
  control: null,
  busy: false,
};

const VIEW_LABELS = {
  home: "Home",
  payments: "Payments",
  incidents: "Incidents",
  recoveries: "Recoveries",
  policy: "Policy & Safety",
  exceptions: "Exceptions",
  outcomes: "Outcomes",
  evaluation: "Evaluation",
  incident: "Incident detail",
};

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function money(paise) {
  const value = Number(paise || 0) / 100;
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(value);
}

function pct(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return `${(number * 100).toFixed(1)}%`;
}

function badge(text, cls = "") {
  return `<span class="badge ${cls}">${esc(text)}</span>`;
}

function showToast(message, error = false) {
  if (!toast) return;
  toast.textContent = message;
  toast.classList.toggle("error", error);
  toast.hidden = false;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => { toast.hidden = true; }, 3200);
}

function activeNav(view) {
  const navView = view === "incident" ? "incidents" : view;
  document.querySelectorAll("[data-nav]").forEach((item) => {
    item.classList.toggle("active", item.dataset.nav === navView);
  });
  if (crumb) crumb.textContent = VIEW_LABELS[view] || "ReRoute Sentinel";
}

function setView(view) {
  state.view = VIEW_LABELS[view] ? view : "home";
  activeNav(state.view);
  window.history.replaceState(null, "", `#${state.view}`);
  render();
}

async function loadCore() {
  state.busy = true;
  render();
  try {
    const [dashboard, incidents, replay] = await Promise.all([
      getDashboard(),
      listIncidents(),
      getReplayStatus().catch(() => null),
    ]);
    state.dashboard = dashboard;
    state.incidents = incidents;
    state.replay = replay;
    if (state.selectedIncidentId) await loadIncident(state.selectedIncidentId, false);
  } catch (error) {
    root.innerHTML = `<div class="card error-card"><div class="eyebrow">Operator console</div><h1>Sentinel could not load</h1><p class="sub">${esc(error.message || "Backend request failed")}</p><button class="btn" data-action="refresh">Retry</button></div>`;
    showToast(error.message || "Backend request failed", true);
  } finally {
    state.busy = false;
    render();
  }
}

async function loadIncident(incidentId, navigate = true) {
  state.selectedIncidentId = incidentId;
  state.busy = true;
  if (navigate) setView("incident"); else render();
  try {
    const [incident, control] = await Promise.all([
      getIncident(incidentId),
      getIncidentControl(incidentId).catch(() => null),
    ]);
    state.incident = incident;
    state.control = control;
  } catch (error) {
    showToast(error.message || "Incident could not be loaded", true);
  } finally {
    state.busy = false;
    render();
  }
}

function pageHeader(eyebrow, title, subtitle, actions = "") {
  return `<div class="page-header"><div><div class="eyebrow">${esc(eyebrow)}</div><h1>${esc(title)}</h1><div class="sub">${esc(subtitle)}</div></div><div class="view-actions">${actions}</div></div>`;
}

function kpis() {
  const d = state.dashboard || {};
  const population = d.population || {};
  const executive = d.executive || {};
  const successRate = population.total ? Number(population.captured || 0) / Number(population.total) : 0;
  return `<div class="grid kpis">
    <div class="card kpi"><div class="kpi-label">Payment volume</div><div class="kpi-value">${esc(population.total ?? 0)}</div><div class="kpi-meta">persisted payment events</div></div>
    <div class="card kpi"><div class="kpi-label">Success rate</div><div class="kpi-value">${pct(successRate)}</div><div class="kpi-meta">${esc(population.captured ?? 0)} captured</div></div>
    <div class="card kpi"><div class="kpi-label">Failures</div><div class="kpi-value ${Number(population.failed || 0) ? "negative" : ""}">${esc(population.failed ?? 0)}</div><div class="kpi-meta">${pct(population.failure_rate || 0)} failure rate</div></div>
    <div class="card kpi"><div class="kpi-label">Revenue at risk</div><div class="kpi-value attention">${money(executive.revenue_at_risk)}</div><div class="kpi-meta">${esc(executive.revenue_at_risk_claim_tag || "current open cases")}</div></div>
    <div class="card kpi"><div class="kpi-label">Actual recovered</div><div class="kpi-value positive">${money(executive.test_mode_value)}</div><div class="kpi-meta">provider-backed Test Mode Outcomes</div></div>
  </div>`;
}

function incidentHeadline(incident) {
  const method = incident?.method || incident?.cohort_filter?.method || "payment";
  return `${String(method).toUpperCase()} payment degradation`;
}

function incidentCard(incident) {
  if (!incident) {
    return `<div class="card incident-card" style="border-color:#dfe6e1;background:#fcfffd"><div class="incident-head"><div><div class="eyebrow">Sentinel status</div><div class="incident-title">No active incident detected</div><div class="incident-copy">Persisted payment health has no visible Sentinel incident requiring review.</div></div>${badge("HEALTHY", "success")}</div></div>`;
  }
  const baseline = incident.baseline_metrics?.success_rate;
  const observed = incident.observed_metrics?.success_rate;
  return `<div class="card incident-card"><div class="incident-head"><div><div class="eyebrow">${esc(incident.state)} incident</div><div class="incident-title">${esc(incidentHeadline(incident))}</div><div class="incident-copy">Detector ${esc(incident.detection_version || "—")} · ${esc(incident.affected_attempt_count || 0)} affected attempts</div></div>${badge(`${Math.round(Number(incident.confidence || 0) * 100)}% CONFIDENCE`)}</div>
    <div class="incident-metrics"><div class="metric-box"><strong>${pct(baseline)}</strong><span>Normal baseline</span></div><div class="metric-box"><strong class="negative">${pct(observed)}</strong><span>Affected window</span></div><div class="metric-box"><strong>${esc(incident.failed_attempt_count ?? incident.affected_attempt_count ?? 0)}</strong><span>Failed attempts</span></div><div class="metric-box"><strong>${esc((incident.method || "—").toUpperCase())}</strong><span>Affected method</span></div></div>
    <div class="impact-block"><div><div class="impact-value">${money(incident.estimated_amount_at_risk)}</div><div class="impact-label">ESTIMATED REVENUE AT RISK</div><div class="exact">${money(incident.amount_affected_paise)} linked failed-attempt value</div></div><button class="btn primary" data-review-incident="${esc(incident.incident_id)}">Review incident →</button></div></div>`;
}

function homeView() {
  const incident = state.incidents[0] || null;
  const replayAction = `<button class="btn primary" data-action="run-replay" ${state.busy ? "disabled" : ""}>Run merchant replay</button><a class="btn" href="/storefront">Open storefront</a>`;
  return `${pageHeader("Operator console", incident ? `${state.incidents.length} payment incident${state.incidents.length === 1 ? "" : "s"} need attention` : "Payments are healthy", incident ? "Sentinel detected persisted payment degradation and assembled an incident for review." : "Sentinel is reading real payment state and will surface deterministic degradation when it exists.", replayAction)}${kpis()}${incidentCard(incident)}<div style="height:16px"></div>${providerEvidencePanel()}`;
}

function providerEvidencePanel() {
  const evidence = state.dashboard?.provider_evidence || {};
  return `<div class="grid content-grid"><div class="card panel"><div class="panel-header"><div><div class="panel-title">Provider evidence</div><div class="panel-sub">Authoritative outcome boundary</div></div>${evidence.present ? badge(evidence.claim_tag || "TEST MODE", "test") : badge("NO PROVIDER OUTCOME")}</div><div class="facts"><div class="fact"><div class="check">${evidence.present ? "✓" : "·"}</div><div><b>${evidence.present ? esc(evidence.signature_boundary) : "No signed Razorpay Test Mode outcome is persisted yet"}</b><span>Actual recovered remains driven by provider-backed Outcome rows.</span></div></div></div></div><div class="card panel"><div class="panel-title">Replay state</div><div class="sub">${esc(state.replay?.status || state.replay?.state || "Replay status is not available")}</div></div></div>`;
}

function paymentRows() {
  const entries = [];
  for (const timeline of state.dashboard?.timeline || []) {
    for (const event of timeline.events || []) {
      if (event.kind === "raw event" && event.data) entries.push(event.data);
    }
  }
  return entries.sort((a, b) => String(b.occurred_at || "").localeCompare(String(a.occurred_at || ""))).slice(0, 30);
}

function paymentsView() {
  const rows = paymentRows();
  const body = rows.length ? rows.map((p) => `<tr><td class="money">${money(p.amount)}</td><td><span class="status ${p.status === "captured" ? "ok" : p.status === "failed" ? "fail" : "proc"}">${esc(p.status || "—")}</span></td><td>${esc(p.method || "—")}</td><td>${esc(p.provider || "—")}</td><td>${esc(p.payment_id || "—")}</td><td>${esc(p.obligation_reference || "—")}</td></tr>`).join("") : `<tr><td colspan="6"><div class="empty"><strong>No normalized payment rows yet</strong>Run the merchant replay or use the Test Mode storefront.</div></td></tr>`;
  return `${pageHeader("Payment operations", "Payments", "Recent normalized payment evidence from persisted RecoveryCase timelines.")}<div class="card panel"><div class="table-wrap"><table class="table"><thead><tr><th>Amount</th><th>Status</th><th>Method</th><th>Provider</th><th>Payment</th><th>Obligation</th></tr></thead><tbody>${body}</tbody></table></div></div>`;
}

function incidentsView() {
  const cards = state.incidents.length ? state.incidents.map(incidentCard).join("") : `<div class="card panel"><div class="empty"><strong>No visible incidents</strong>Run a real merchant replay to let the deterministic detector evaluate payment health.</div></div>`;
  return `${pageHeader("Payment health", "Incidents", "Abnormal payment behavior grouped into persisted incidents, ordered by recency.")}${cards}`;
}

function recommendationRows(control) {
  const rec = control?.recommendation;
  const selectedCase = rec?.case_recommendations?.find((item) => item.case_id === rec.recommended_case_id) || rec?.case_recommendations?.[0];
  return selectedCase || null;
}

function policyPanel() {
  const caseRec = recommendationRows(state.control);
  const allowed = caseRec?.allowed_actions || [];
  const blocked = caseRec?.blocked_actions || [];
  const blockedMap = new Map(blocked.map((item) => [item.action, item]));
  const retry = blockedMap.get("retry");
  const allowedRows = allowed.slice(0, 4).map((action) => `<div class="policy-row"><div><div class="policy-name">${esc(action)}</div><div class="policy-reason">Permitted by deterministic Policy for the current context.</div></div><span class="policy-chip allowed">ALLOWED</span></div>`).join("");
  const retryRow = retry ? `<div class="policy-row blocked"><div><div class="policy-name">Retry</div><div class="policy-reason">${esc((retry.reasons || []).join(", ") || "Policy blocked")}. <b>Removed before AI ranking.</b></div></div><span class="policy-chip blocked">BLOCKED</span></div>` : "";
  return `<div class="card section-card"><div class="panel-header"><div><h2 class="section-title" style="margin:0">Policy &amp; safety</h2><div class="panel-sub">Evaluated before action ranking</div></div>${badge("POLICY")}</div><div class="policy-stack">${allowedRows || `<div class="policy-row"><div><div class="policy-name">No action permitted</div><div class="policy-reason">Current deterministic context has no executable recovery action.</div></div></div>`}${retryRow}</div></div>`;
}

function factsPanel() {
  const incident = state.incident || {};
  const bundle = incident.evidence_bundle || {};
  const facts = [
    [`${incident.affected_attempt_count ?? 0} payment attempts are in the affected cohort`, `Method: ${incident.method || "unknown"} · source: ${incident.source_kind || "unknown"}`],
    [`Success rate moved from ${pct(incident.baseline_metrics?.success_rate)} to ${pct(incident.observed_metrics?.success_rate)}`, "Observed detector metrics; not model inference"],
    [`${money(incident.amount_affected_paise)} linked failed-attempt value`, "Revenue-at-risk remains ESTIMATED until provider outcome evidence"],
    [`${incident.linked_event_ids?.length || 0} immutable events linked`, `${incident.case_chain?.length || 0} recovery cases correlated`],
  ];
  return `<div class="card section-card"><div class="panel-header"><div><h2 class="section-title" style="margin:0">Verified facts</h2><div class="panel-sub">Deterministic evidence from persisted payment events</div></div>${badge("OBSERVED")}</div><div class="facts">${facts.map(([title, detail]) => `<div class="fact"><div class="check">✓</div><div><b>${esc(title)}</b><span>${esc(detail)}</span></div></div>`).join("")}</div><details class="technical"><summary>View technical evidence</summary><div class="technical-grid"><div class="technical-item"><span>Incident ID</span><strong>${esc(incident.incident_id)}</strong></div><div class="technical-item"><span>Detection version</span><strong>${esc(incident.detection_version || "—")}</strong></div><div class="technical-item"><span>Source kind</span><strong>${esc(incident.source_kind || bundle.source_kind || "—")}</strong></div><div class="technical-item"><span>Provenance</span><strong>${esc(JSON.stringify(incident.provenance_summary || {}))}</strong></div></div></details></div>`;
}

function aiPanel() {
  const analysis = state.control?.analysis;
  const result = analysis?.result || analysis || {};
  const summary = result.summary || "No bounded AI analysis has been persisted for this incident yet.";
  const hypotheses = result.hypotheses || [];
  const steps = result.next_validation_steps || [];
  return `<div class="card section-card ai-card"><div class="panel-header"><div><h2 class="section-title" style="margin:0">What Sentinel thinks</h2><div class="panel-sub">Advisory analysis over sanitized incident evidence</div></div><span class="ai-label">AI ANALYSIS — ADVISORY</span></div><div class="analysis-text">${esc(summary)}</div><div class="confidence"><span>Hypotheses</span><strong>${esc(hypotheses.join(" · ") || "Not established")}</strong></div><div class="confidence"><span>Next validation</span><strong>${esc(steps.join(" · ") || "Use deterministic evidence and provider telemetry")}</strong></div></div>`;
}

function auditPanel() {
  const rows = state.incident?.audit || [];
  return `<div class="card section-card"><h2 class="section-title">Readable audit trail</h2><div class="timeline">${rows.length ? rows.slice(-12).map((event) => `<div class="timeline-row"><div class="timeline-node done">✓</div><div class="timeline-copy"><b>${esc(event.event_type)}</b><span>${esc(event.payload?.reason || event.payload?.action || event.payload?.case_id || "Persisted audit evidence")}</span></div><div class="timeline-time">${esc(event.created_at ? new Date(event.created_at).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"}) : "")}</div></div>`).join("") : `<div class="empty"><strong>No audit events yet</strong>The incident has no persisted control-plane audit entries.</div>`}</div></div>`;
}

function recommendationPanel() {
  const control = state.control || {};
  const rec = control.recommendation || {};
  const action = rec.recommended_action || control.recommended_action;
  const controlState = control.control_state || "no_action_available";
  let controls = "";
  if (!action) {
    controls = `<div class="approval-box"><p>No policy-permitted recommendation is available yet.</p><button class="btn primary" data-action="investigate-incident">Investigate incident</button></div>`;
  } else if (controlState === "needs_approval") {
    controls = `<div class="approval-box"><div style="font-weight:700">Human approval required</div><p>Sentinel cannot execute the consequential action until the business owner approves the current recommendation.</p><button class="btn primary" style="width:100%" data-action="approve-incident">Approve recommended action</button></div>`;
  } else if (controlState === "approved") {
    controls = `<div class="approval-box"><div class="success-panel"><strong>Approved by business owner</strong><div class="rec-copy" style="margin-top:4px">Approval is bound to this recommendation, Policy version and context hash.</div></div><button class="btn primary" style="width:100%;margin-top:10px" data-action="execute-incident">Execute approved action</button></div>`;
  } else if (controlState === "awaiting_outcome") {
    controls = `<div class="approval-box"><div class="success-panel"><strong>Action executed</strong><div class="rec-copy" style="margin-top:4px">Awaiting provider Outcome. Actual recovered is still ${money(control.actual_recovered_amount_paise)}.</div></div><div class="progress"><div class="progress-row done"><span class="progress-dot">✓</span>Evidence loaded</div><div class="progress-row done"><span class="progress-dot">✓</span>Policy evaluated</div><div class="progress-row done"><span class="progress-dot">✓</span>Human approval recorded</div><div class="progress-row active"><span class="progress-dot">•</span>Awaiting provider Outcome</div></div></div>`;
  } else if (controlState === "recovered") {
    controls = `<div class="approval-box"><div class="success-panel"><strong>Provider-verified Outcome</strong><div class="impact-value" style="font-size:25px;margin-top:7px">${money(control.actual_recovered_amount_paise)}</div><div class="rec-copy">${esc(control.actual_recovered_claim_tag || "TEST MODE")}</div></div></div>`;
  }
  return `<div class="card section-card"><h2 class="section-title">Recommended action</h2><div class="recommendation"><div class="rec-top"><div><div class="recommended">RECOMMENDED</div><div class="rec-name">${esc(action || "No action yet")}</div></div>${badge(action ? "APPROVAL REQUIRED" : "INVESTIGATION")}</div><div class="rec-copy">Selected only from deterministic Policy-permitted actions. AI does not grant authority.</div></div>${controls}</div>`;
}

function incidentDetailView() {
  if (!state.incident) return `<div class="card loading-card"><h1>${state.busy ? "Loading incident…" : "No incident selected"}</h1></div>`;
  const i = state.incident;
  return `${pageHeader(`${i.state} incident · ${Math.round(Number(i.confidence || 0)*100)}% confidence`, incidentHeadline(i), "Deterministic facts, advisory AI analysis and bounded recovery remain visibly separate.", `<button class="btn" data-nav="incidents">← Back to incidents</button>`)}<div class="incident-metrics"><div class="metric-box"><strong>${pct(i.baseline_metrics?.success_rate)}</strong><span>Baseline success</span></div><div class="metric-box"><strong class="negative">${pct(i.observed_metrics?.success_rate)}</strong><span>Incident success</span></div><div class="metric-box"><strong>${esc(i.affected_attempt_count || 0)}</strong><span>Affected payments</span></div><div class="metric-box"><strong>${money(i.estimated_amount_at_risk)}</strong><span>ESTIMATED at risk</span></div></div><div class="incident-layout"><div class="grid">${factsPanel()}${aiPanel()}${auditPanel()}</div><aside class="grid">${policyPanel()}${recommendationPanel()}<div class="card section-card"><h2 class="section-title">Truth boundary</h2><div class="facts"><div class="fact"><div class="check" style="background:#f5f3ff;color:#6347a4">S</div><div><b>SIMULATED</b><span>Merchant replay when source_kind is simulated</span></div></div><div class="fact"><div class="check" style="background:#fff7e6;color:#936210">E</div><div><b>ESTIMATED</b><span>Revenue at risk</span></div></div><div class="fact"><div class="check" style="background:#eef5ff;color:#2855a4">T</div><div><b>TEST MODE</b><span>Only authenticated Razorpay Test Mode evidence can prove recovered revenue</span></div></div></div></div></aside></div>`;
}

function recoveriesView() {
  const worklist = state.dashboard?.worklist || [];
  const groups = {
    "Needs approval": worklist.filter((item) => item.state === "eligible"),
    "In progress": worklist.filter((item) => ["action_selected"].includes(item.state)),
    "Awaiting outcome": worklist.filter((item) => item.state === "awaiting_outcome"),
    "Recovered": worklist.filter((item) => item.state === "recovered"),
  };
  return `${pageHeader("Recovery operations", "Recoveries", "Persisted RecoveryCases grouped by the decision or outcome state they actually occupy.")}<div class="grid" style="grid-template-columns:repeat(2,minmax(0,1fr))">${Object.entries(groups).map(([name, rows]) => `<div class="card section-card"><div class="panel-header"><div><div class="panel-title">${esc(name)}</div><div class="panel-sub">${rows.length} case${rows.length === 1 ? "" : "s"}</div></div>${badge(`${rows.length} ITEM${rows.length === 1 ? "" : "S"}`)}</div>${rows.slice(0,4).map((item) => `<div class="policy-row"><div><div class="policy-name">${esc(item.selected_action || item.case_id)}</div><div class="policy-reason">${money(item.amount_at_risk)} · ${esc(item.state)}</div></div></div>`).join("") || `<div class="empty"><strong>No cases</strong>Nothing is in this state.</div>`}</div>`).join("")}</div>`;
}

function policyView() {
  const settings = state.dashboard?.policy_settings || {};
  const blocked = (state.dashboard?.worklist || []).flatMap((item) => Object.entries(item.blocked_reasons || {}).map(([action, reasons]) => ({ action, reasons, caseId:item.case_id }))).slice(0,8);
  return `${pageHeader("Deterministic control plane", "Policy & Safety", "Rules are evaluated before any ranking layer and remain independent of model preference.", badge("POLICY BEFORE AI"))}<div class="grid content-grid"><div class="card section-card"><h2 class="section-title">Current policy configuration</h2><div class="facts"><div class="fact"><div class="check">✓</div><div><b>Contact limit: ${esc(settings.contact_limit ?? "—")}</b><span>Persistent deterministic guardrail</span></div></div><div class="fact"><div class="check">✓</div><div><b>Quiet hours: ${esc(settings.quiet_hours_start ?? "—")}:00 → ${esc(settings.quiet_hours_end ?? "—")}:00</b><span>Policy version ${esc(settings.policy_version || settings.version || "—")}</span></div></div><div class="fact"><div class="check">✓</div><div><b>Kill switch: ${settings.kill_switch ? "ON" : "OFF"}</b><span>Execution is fail-closed when enabled.</span></div></div></div></div><div class="card section-card"><h2 class="section-title">Blocked before ranking</h2><div class="policy-stack">${blocked.length ? blocked.map((row) => `<div class="policy-row blocked"><div><div class="policy-name">${esc(row.action)}</div><div class="policy-reason">${esc(row.reasons.join(", "))} · ${esc(row.caseId)} · <b>Removed before AI ranking.</b></div></div><span class="policy-chip blocked">BLOCKED</span></div>`).join("") : `<div class="empty"><strong>No current blocked-action rows</strong>Blocking will appear when a case context triggers a deterministic rule.</div>`}</div></div></div>`;
}

function exceptionsView() {
  const rows = state.dashboard?.payment_exceptions || [];
  return `${pageHeader("Review queue", "Exceptions", "Uncertain payment states that require explicit operator evidence and resolution.")}<div class="card panel">${rows.length ? `<div class="table-wrap"><table class="table"><thead><tr><th>Case</th><th>Kind</th><th>State</th><th>Resolution</th></tr></thead><tbody>${rows.map((x) => `<tr><td>${esc(x.case_id)}</td><td>${esc(x.kind)}</td><td>${esc(x.state)}</td><td>${esc(x.resolution || "—")}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty"><strong>No unresolved exception requires action</strong>Missing evidence is not converted into a recovery claim.</div>`}</div>`;
}

function outcomesView() {
  const d = state.dashboard || {};
  const recovered = d.executive?.test_mode_value || 0;
  const awaiting = (d.worklist || []).filter((item) => item.state === "awaiting_outcome").length;
  return `${pageHeader("Verified business outcomes", "Outcomes", "Estimated opportunity is never mixed into recovered revenue.", badge("PROVIDER EVIDENCE REQUIRED", "test"))}<div class="grid kpis" style="grid-template-columns:repeat(3,minmax(0,1fr))"><div class="card kpi"><div class="kpi-label">Actual recovered</div><div class="kpi-value">${money(recovered)}</div><div class="kpi-meta">Razorpay Test Mode Outcomes only</div></div><div class="card kpi"><div class="kpi-label">Provider events</div><div class="kpi-value">${esc(d.population?.test_mode_events ?? 0)}</div><div class="kpi-meta">persisted Test Mode events</div></div><div class="card kpi"><div class="kpi-label">Awaiting outcome</div><div class="kpi-value">${awaiting}</div><div class="kpi-meta">provider evidence pending</div></div></div>${providerEvidencePanel()}`;
}

function evaluationView() {
  const evaluation = state.dashboard?.evaluation;
  if (!evaluation) return `${pageHeader("SIMULATED EVALUATION", "Evaluation", "No persisted evaluation comparison is available yet.", badge("SIMULATED EVALUATION", "sim"))}<div class="card panel"><div class="empty"><strong>Evaluation output unavailable</strong>Run the deterministic benchmark before showing comparative numbers.</div></div>`;
  const rows = Array.isArray(evaluation) ? evaluation : evaluation.strategies || evaluation.results || evaluation.comparisons || [];
  return `${pageHeader("SIMULATED EVALUATION", "Evaluation", "Reproducible benchmark output from the backend; not production merchant lift.", badge("SIMULATED EVALUATION", "sim"))}<div class="card panel">${rows.length ? `<div class="table-wrap"><table class="table"><thead><tr><th>Strategy</th><th>Recovered</th><th>Rate</th><th>Actions</th><th>Violations</th></tr></thead><tbody>${rows.map((r) => `<tr><td><strong>${esc(r.strategy || r.name || "strategy")}</strong></td><td class="money">${money(r.recovered_amount ?? r.recovered_amount_paise ?? 0)}</td><td>${r.recovery_rate == null ? "—" : pct(r.recovery_rate)}</td><td>${esc(r.actions ?? r.action_count ?? "—")}</td><td>${esc(r.policy_violations ?? r.violations ?? "—")}</td></tr>`).join("")}</tbody></table></div>` : `<div class="technical-grid">${Object.entries(evaluation).slice(0,12).map(([key,value]) => `<div class="technical-item"><span>${esc(key.replaceAll("_"," "))}</span><strong>${esc(typeof value === "object" ? JSON.stringify(value) : value)}</strong></div>`).join("")}</div>`}</div>`;
}

function render() {
  if (!root) return;
  activeNav(state.view);
  if (state.busy && !state.dashboard && state.view !== "incident") {
    root.innerHTML = `<div class="card loading-card"><div class="eyebrow">Operator console</div><h1>Loading ReRoute Sentinel…</h1><p class="sub">Reading persisted payments, incidents, policy and outcomes.</p></div>`;
    return;
  }
  const views = {
    home: homeView,
    payments: paymentsView,
    incidents: incidentsView,
    incident: incidentDetailView,
    recoveries: recoveriesView,
    policy: policyView,
    exceptions: exceptionsView,
    outcomes: outcomesView,
    evaluation: evaluationView,
  };
  root.innerHTML = (views[state.view] || homeView)();
}

async function mutate(action, successMessage) {
  if (state.busy) return;
  state.busy = true;
  render();
  try {
    await action();
    showToast(successMessage);
    await loadCore();
    if (state.selectedIncidentId) await loadIncident(state.selectedIncidentId, false);
  } catch (error) {
    showToast(error.message || "Action failed", true);
  } finally {
    state.busy = false;
    render();
  }
}

document.addEventListener("click", (event) => {
  const nav = event.target.closest("[data-nav]");
  if (nav && nav.dataset.nav) {
    event.preventDefault();
    setView(nav.dataset.nav);
    return;
  }
  const review = event.target.closest("[data-review-incident]");
  if (review) {
    loadIncident(review.dataset.reviewIncident);
    return;
  }
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (action === "refresh") loadCore();
  if (action === "run-replay") mutate(() => runReplay(), "Merchant replay completed from backend state.");
  if (action === "investigate-incident" && state.selectedIncidentId) mutate(() => investigateIncident(state.selectedIncidentId, `ui-${state.selectedIncidentId}-${Date.now()}`), "Incident investigation persisted.");
  if (action === "approve-incident" && state.selectedIncidentId) mutate(() => approveIncident(state.selectedIncidentId), "Current recommendation approved.");
  if (action === "execute-incident" && state.selectedIncidentId) mutate(() => executeIncident(state.selectedIncidentId), "Approved action executed; awaiting provider Outcome.");
});

const initialView = window.location.hash.replace("#", "");
if (VIEW_LABELS[initialView] && initialView !== "incident") state.view = initialView;
activeNav(state.view);
loadCore();
