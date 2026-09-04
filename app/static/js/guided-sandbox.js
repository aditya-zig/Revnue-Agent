const $ = (id) => document.getElementById(id);
const money = (paise) => new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(Number(paise || 0) / 100);
const pct = (value) => `${(Number(value || 0) * 100).toFixed(1)}%`;

const state = {
  incident: null,
  control: null,
  providerLink: null,
  replayId: null,
  runId: null,
  incidentId: null,
  freeExplore: false,
  polling: null,
};

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("Accept", "application/json");
  let body = options.body;
  if (body && typeof body !== "string") {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(body);
  }
  const response = await fetch(`/api/v1${path}`, { ...options, headers, body, credentials: "same-origin" });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = payload && typeof payload === "object" ? payload.detail : payload;
    const message = typeof detail === "string" ? detail : `Request failed with status ${response.status}`;
    const error = new Error(message);
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}

function formatRisk(paise) {
  const rupees = Number(paise || 0) / 100;
  if (rupees >= 100000) return `₹${(rupees / 100000).toFixed(2)}L`;
  if (rupees >= 1000) return `₹${(rupees / 1000).toFixed(1)}K`;
  return money(paise);
}

function escapeHTML(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function addEvent(title, detail = "") {
  const root = $("eventLog");
  const node = document.createElement("div");
  node.className = "event";
  node.innerHTML = `<b>${escapeHTML(title)}</b><span>${escapeHTML(detail)}</span>`;
  root.prepend(node);
  while (root.children.length > 7) root.lastElementChild.remove();
}

function addActivity(amount, method, status) {
  const root = $("activityRows");
  const row = document.createElement("div");
  row.className = "row";
  row.innerHTML = `<div><strong>${escapeHTML(amount)}</strong><small>${escapeHTML(method)}</small></div><span class="pill ${status === "Failed" ? "fail" : ""}">${escapeHTML(status)}</span>`;
  root.prepend(row);
  while (root.children.length > 5) root.lastElementChild.remove();
}

function replayIdentity() {
  let token;
  if (globalThis.crypto?.randomUUID) {
    token = globalThis.crypto.randomUUID().replaceAll("-", "");
  } else {
    token = `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
  }
  return `guided_${token}`.slice(0, 64);
}

function seedHealthyUI() {
  $("healthTitle").textContent = "Payments are healthy";
  $("healthSupport").textContent = "Sentinel is watching recent payment attempts.";
  $("successRate").textContent = "91.8%";
  $("baselineRate").textContent = "91.8%";
  $("affectedCount").textContent = "0";
  $("actualRecovered").textContent = "₹0";
  $("healthBadge").textContent = "Payments are healthy";
  $("healthBadge").className = "badge ok";
  $("razorpayState").textContent = "RAZORPAY TEST MODE";
  $("sandboxTrend").setAttribute("d", "M0 111 C80 103 135 111 190 101 S310 111 365 102 S475 108 535 99 S665 111 730 101 S840 103 900 94");
  $("activityRows").innerHTML = "";
  $("eventLog").innerHTML = "";
  addActivity("₹899", "UPI", "Captured");
  addActivity("₹2,499", "UPI", "Captured");
  addActivity("₹1,299", "Card", "Captured");
  addActivity("₹749", "UPI", "Captured");
  addEvent("SIMULATED DEMO DATA", "Payment health is within the replay merchant baseline.");
  $("guideTitle").textContent = "Replay a simulated merchant day";
  $("guideCopy").textContent = "Use SIMULATED DEMO DATA to watch the real detector, investigation, Policy, approval, and provider-evidence boundaries.";
  $("guideAction").textContent = "Start simulated replay";
  $("guideAction").disabled = false;
  state.incident = null;
  state.control = null;
  state.providerLink = null;
  state.replayId = null;
  state.runId = null;
  state.incidentId = null;
  stopOutcomePolling();
}

function startStory() {
  const overlay = $("storyOverlay");
  const card = $("storyCard");
  const progress = $("storyProgress");
  const trend = $("storyTrend");
  let timers = [];
  const clear = () => timers.forEach(window.clearTimeout);
  const later = (ms, fn) => timers.push(window.setTimeout(fn, ms));
  const run = () => {
    clear();
    overlay.classList.remove("show");
    $("storyStatus").textContent = "SIMULATED PRODUCT STORY";
    $("storyRate").textContent = "91.8%";
    $("storyIncidents").textContent = "0";
    $("storyFeed").innerHTML = `<div class="row"><div><strong>₹899</strong><small>UPI</small></div><span class="pill">Captured</span></div><div class="row"><div><strong>₹1,299</strong><small>Card</small></div><span class="pill">Captured</span></div><div class="row"><div><strong>₹2,499</strong><small>UPI</small></div><span class="pill">Captured</span></div>`;
    trend.setAttribute("d", "M0 88 C80 82 120 88 170 80 S270 89 320 81 S430 86 480 78 S600 87 700 76");
    progress.style.transition = "none";
    progress.style.width = "0";
    requestAnimationFrame(() => { progress.style.transition = "width 12s linear"; progress.style.width = "100%"; });
    later(3300, () => {
      $("storyRate").textContent = "58.3%";
      $("storyIncidents").textContent = "1";
      $("storyStatus").textContent = "SIMULATED PRODUCT STORY";
      trend.setAttribute("d", "M0 88 C100 82 180 84 270 82 S390 88 470 83 C535 84 570 110 610 150 S660 184 700 191");
      $("storyFeed").insertAdjacentHTML("afterbegin", `<div class="row"><div><strong>₹2,499</strong><small>UPI</small></div><span class="pill fail">Failed</span></div>`);
    });
    later(5600, () => {
      card.innerHTML = `<div class="eyebrow">SIMULATED PRODUCT STORY</div><h3>UPI payment degradation detected</h3><div class="amount">₹46.2K</div><div class="story-copy">ESTIMATED AT RISK · 91.8% baseline → 58.3% current</div>`;
      overlay.classList.add("show");
    });
    later(7900, () => {
      card.innerHTML = `<div class="eyebrow">SIMULATED PRODUCT STORY · POLICY</div><h3>Unsafe actions are removed first.</h3><div class="story-policy"><div><span>Send alternate payment link</span><span class="badge ok">ALLOWED</span></div><div class="blocked"><span>Retry hard-decline cases</span><span class="badge block">BLOCKED</span></div></div>`;
    });
    later(10100, () => {
      card.innerHTML = `<div class="eyebrow">SIMULATED PRODUCT STORY</div><div class="amount">₹0</div><h3>ACTUAL RECOVERED</h3><div class="story-copy">₹0 ACTUAL RECOVERED · Awaiting provider evidence</div>`;
    });
    later(12300, run);
  };
  run();
}

function openSandbox() {
  $("sandbox").classList.add("active");
  $("sandbox").setAttribute("aria-hidden", "false");
  seedHealthyUI();
}

function closeSandbox() {
  $("sandbox").classList.remove("active");
  $("sandbox").setAttribute("aria-hidden", "true");
  stopOutcomePolling();
}

async function getIncident() {
  if (!state.incidentId) throw new Error("No sandbox incident is bound to this browser.");
  return api(`/incidents/${encodeURIComponent(state.incidentId)}`);
}

async function getControl() {
  if (!state.incidentId) throw new Error("No sandbox incident is bound to this browser.");
  return api(`/incidents/${encodeURIComponent(state.incidentId)}/control`);
}

function assertIncidentBinding(incident) {
  if (incident?.incident_id !== state.incidentId) {
    throw new Error("Incident response did not match this sandbox run.");
  }
  if (incident?.cohort_filter?.run_id !== state.runId || incident?.cohort_filter?.replay_id !== state.replayId) {
    throw new Error("Incident evidence belongs to a different sandbox run.");
  }
}

function applyIncident(incident) {
  state.incident = incident;
  const baseline = Number(incident?.baseline_metrics?.success_rate ?? 0);
  const current = Number(incident?.observed_metrics?.success_rate ?? 0);
  const affected = Number(incident?.affected_attempt_count ?? incident?.failed_attempt_count ?? 0);
  const risk = Number(incident?.peak_estimated_amount_at_risk_paise ?? incident?.estimated_amount_at_risk ?? 0);
  const method = String(incident?.method || incident?.cohort_filter?.method || "payment").toUpperCase();
  $("healthTitle").textContent = `Unusual ${method} degradation detected`;
  $("healthSupport").textContent = "Success rate moved outside the merchant's normal range.";
  $("successRate").textContent = pct(current);
  $("baselineRate").textContent = pct(baseline);
  $("affectedCount").textContent = String(affected);
  $("healthBadge").textContent = "Incident detected";
  $("healthBadge").className = "badge warn";
  $("sandboxTrend").setAttribute("d", "M0 111 C110 104 210 106 315 102 S500 108 570 109 C645 112 690 160 735 205 S830 250 900 260");
  $("incidentRisk").textContent = formatRisk(risk);
  $("incidentCompare").textContent = `${pct(baseline)} baseline → ${pct(current)} current · ${affected} payments affected`;
  $("reviewRisk").textContent = `${formatRisk(risk)} estimated at risk`;
  $("factBaseline").textContent = pct(baseline);
  $("factCurrent").textContent = pct(current);
  $("factAffected").textContent = String(affected);
  $("factMethod").textContent = method;
  $("factsCopy").textContent = `Success rate dropped below the merchant baseline. ${affected} linked payment attempts are in this incident window. ${money(risk)} is estimated exposure, not recovered revenue.`;
}

function actionLabel(action) {
  const labels = {
    payment_link: "Send alternate payment link",
    retry: "Retry payment",
    contact: "Contact customer",
    promise: "Record promise to pay",
    escalate: "Escalate to human review",
  };
  return labels[action] || String(action || "Unknown action").replaceAll("_", " ");
}

function analysisText(control) {
  const analysis = control?.analysis || control?.incident_analysis || null;
  if (!analysis) {
    return {
      text: "Sentinel completed bounded incident analysis. Root cause remains unconfirmed.",
      meta: "No external AI result was asserted as fact.",
    };
  }
  const result = analysis.result || analysis.output || analysis;
  const hypotheses = result?.hypotheses;
  let text = "Sentinel completed bounded incident analysis. Root cause remains unconfirmed.";
  if (Array.isArray(hypotheses) && hypotheses.length) {
    const first = hypotheses[0];
    text = typeof first === "string" ? first : first?.statement || text;
  }
  const metadata = analysis.provider_metadata || result?.model_metadata || analysis.model_metadata || {};
  const fallback = Boolean(metadata.fallback_used ?? result?.fallback_used);
  const model = metadata.resolved_model || metadata.requested_model || "configured advisory model";
  return {
    text,
    meta: fallback
      ? "Deterministic fallback used; no external model claim is shown as fact."
      : `Advisory analysis generated by ${model}.`,
  };
}

function selectedCaseRecommendation(control) {
  const recommendation = control?.recommendation;
  const rows = Array.isArray(recommendation?.case_recommendations) ? recommendation.case_recommendations : [];
  return rows.find((row) => row?.case_id === recommendation?.recommended_case_id)
    || rows.find((row) => row?.recommended_action === recommendation?.recommended_action)
    || null;
}

function renderControl(control) {
  state.control = control;
  const copy = analysisText(control);
  $("aiCopy").textContent = copy.text;
  $("aiMeta").textContent = copy.meta;
  $("aiMeta").classList.remove("hidden");

  const recommendation = control?.recommendation || {};
  const selected = selectedCaseRecommendation(control);
  const allowed = Array.isArray(selected?.allowed_actions) ? selected.allowed_actions : [];
  const blocked = Array.isArray(selected?.blocked_actions) ? selected.blocked_actions : [];

  $("policyAllowed").innerHTML = allowed.length
    ? allowed.map((action) => `<div class="policy-box"><div class="line"><strong>${escapeHTML(actionLabel(action))}</strong><span class="badge ok">ALLOWED</span></div></div>`).join("")
    : `<div class="policy-box"><div class="copy">No recovery action is currently permitted.</div></div>`;
  $("policyBlocked").innerHTML = blocked.length
    ? blocked.map((item) => `<div class="policy-box blocked"><div class="line"><strong>${escapeHTML(actionLabel(item.action))}</strong><span class="badge block">BLOCKED</span></div><div class="copy">${escapeHTML(Array.isArray(item.reasons) ? item.reasons.join(" · ") : "Removed before ranking")}</div></div>`).join("")
    : `<div class="policy-box"><div class="copy">No additional actions were blocked for the recommended case.</div></div>`;

  const recommendedAction = recommendation?.recommended_action || selected?.recommended_action || null;
  $("recommendationAction").textContent = recommendedAction ? actionLabel(recommendedAction) : "No action available";
  $("recommendationReason").textContent = selected?.reason || "No recovery action is currently permitted by deterministic Policy.";
  $("approveAction").disabled = !recommendedAction;
}

async function startMerchantIncident() {
  $("guideAction").disabled = true;
  $("guideTitle").textContent = "SIMULATED DEMO DATA replay running";
  $("guideCopy").textContent = "The backend is generating the merchant-day PaymentEvents and running the deterministic detector.";
  addEvent("Replay started", "SIMULATED DEMO DATA is being processed by the real backend detector.");

  state.replayId = replayIdentity();
  const replay = await api(`/replay/start?replay_id=${encodeURIComponent(state.replayId)}&seed=47`, { method: "POST" });
  if (!replay?.run_id || !replay?.incident_id) {
    throw new Error("Replay did not return the exact run and incident identity.");
  }
  state.runId = replay.run_id;
  state.incidentId = replay.incident_id;

  const incident = await getIncident();
  assertIncidentBinding(incident);
  applyIncident(incident);
  addEvent("Payment incident detected", "Deterministic incident evidence is bound to this sandbox run.");
  await showInvestigation();
}

async function showInvestigation() {
  $("dim").classList.add("show");
  $("investFocus").classList.add("show");
  $("investFooter").textContent = "Assembling persisted evidence…";
  const steps = [...document.querySelectorAll("#investSteps .invest-step")];
  for (const step of steps) step.classList.remove("done");

  const detail = await getIncident();
  assertIncidentBinding(detail);
  applyIncident(detail);
  steps.slice(0, 3).forEach((step) => step.classList.add("done"));
  $("investFooter").textContent = "Running bounded analysis and deterministic Policy…";
  $("aiLoading").classList.remove("hidden");

  const key = `guided:${state.runId}:investigate`;
  const control = await api(`/incidents/${encodeURIComponent(state.incidentId)}/investigate`, {
    method: "POST",
    body: { idempotency_key: key },
  });
  renderControl(control);
  $("aiLoading").classList.add("hidden");
  steps.slice(3).forEach((step) => step.classList.add("done"));
  $("investFooter").textContent = "Incident ready for review";
  $("investFocus").classList.remove("show");
  $("incidentFocus").classList.add("show");
}

async function openReview() {
  $("incidentFocus").classList.remove("show");
  $("dim").classList.remove("show");
  try {
    const [detail, control] = await Promise.all([getIncident(), getControl()]);
    assertIncidentBinding(detail);
    applyIncident(detail);
    renderControl(control);
  } catch (error) {
    $("aiMeta").textContent = error.message;
    $("aiMeta").classList.remove("hidden");
  }
  $("reviewPanel").classList.add("show");
}

async function approveAndExecute() {
  $("approveAction").disabled = true;
  try {
    const current = await getControl();
    renderControl(current);
    if (!current?.recommendation?.recommended_action) {
      throw new Error("Deterministic Policy produced no actionable recommendation.");
    }
    await api(`/incidents/${encodeURIComponent(state.incidentId)}/approve`, { method: "POST" });
    $("reviewPanel").classList.remove("show");
    $("awaiting").classList.add("show");
    $("awaitTitle").textContent = "Creating recovery action…";
    $("awaitCopy").textContent = "Approval is not recovery.";
    $("awaitAmount").textContent = "₹0";
    const result = await api(`/incidents/${encodeURIComponent(state.incidentId)}/execute`, { method: "POST" });
    state.providerLink = typeof result?.provider_reference === "string" ? result.provider_reference : null;
    $("awaitTitle").textContent = state.providerLink ? "Payment link created" : "Recovery action created";
    $("awaitCopy").textContent = "Waiting for provider evidence. Approval is not recovery.";
    if (state.providerLink && /^https?:\/\//i.test(state.providerLink)) {
      $("openRecovery").classList.remove("hidden");
    }
    startOutcomePolling();
  } catch (error) {
    $("awaiting").classList.add("show");
    $("awaitTitle").textContent = "Recovery action was not executed";
    $("awaitCopy").textContent = error.message;
    $("approveAction").disabled = false;
  }
}

function openRecoveryLink() {
  if (!state.providerLink || !/^https?:\/\//i.test(state.providerLink)) return;
  window.open(state.providerLink, "reroute-recovery", "noopener,noreferrer");
  $("awaitTitle").textContent = "Waiting for provider evidence";
  $("awaitCopy").textContent = "Sentinel will count recovery only after authoritative provider evidence arrives for this incident.";
}

function startOutcomePolling() {
  stopOutcomePolling();
  const check = async () => {
    try {
      const control = await getControl();
      state.control = control;
      const recovered = Number(control?.actual_recovered_amount_paise || 0);
      $("actualRecovered").textContent = money(recovered);
      if (control?.control_state === "recovered" && recovered > 0 && control?.actual_recovered_claim_tag) {
        providerVerified(control);
        return;
      }
      if (control?.awaiting_provider_evidence) {
        $("awaitAmount").textContent = "₹0";
      }
    } catch (_) {
      // Keep the exact-incident poll best-effort while the user explores.
    }
    state.polling = window.setTimeout(check, 2200);
  };
  check();
}

function stopOutcomePolling() {
  if (state.polling) window.clearTimeout(state.polling);
  state.polling = null;
}

function providerVerified(control) {
  stopOutcomePolling();
  const amount = Number(control?.actual_recovered_amount_paise || 0);
  if (!(amount > 0) || control?.control_state !== "recovered") return;
  $("recoveredAmount").textContent = money(amount);
  $("actualRecovered").textContent = money(amount);
  $("razorpayState").textContent = "RAZORPAY TEST MODE · VERIFIED";
  addEvent("Provider outcome verified", `${money(amount)} recovered in Razorpay Test Mode for this incident.`);
  if (state.freeExplore) {
    $("freeBanner").textContent = `Provider outcome verified · ${money(amount)} recovered`;
    $("freeBanner").classList.add("show");
    refreshFreeViews();
  } else {
    $("recovered").classList.add("show");
  }
}

function switchView(name) {
  const mapping = {
    overview: "overviewView",
    payments: "paymentsView",
    incidents: "incidentsView",
    history: "historyView",
    audit: "auditView",
    evaluation: "evaluationView",
    settings: "settingsView",
  };
  Object.values(mapping).forEach((id) => $(id).classList.add("hidden"));
  $(mapping[name] || mapping.overview).classList.remove("hidden");
  document.querySelectorAll(".nav-item[data-view]").forEach((item) => item.classList.toggle("active", item.dataset.view === name));
  if (state.freeExplore) refreshFreeViews();
}

function auditTitle(eventType) {
  const known = {
    "incident.detected": "Incident detected",
    "incident.analysis.created": "AI advisory created",
    "incident.recommendation.ready": "Recommendation created",
    "incident.approval.granted": "Approved by business owner",
    "incident.execution.completed": "Recovery action executed",
    "incident.execution.failed": "Recovery action failed",
    "incident.recovered": "Provider-backed outcome recorded",
  };
  if (known[eventType]) return known[eventType];
  return String(eventType || "Audit event")
    .replaceAll(".", " ")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function auditDetail(item) {
  const payload = item?.payload || {};
  const parts = [];
  if (payload.action) parts.push(actionLabel(payload.action));
  if (payload.actor_id) parts.push("Business owner authority");
  if (payload.awaiting_provider_evidence) parts.push("Waiting for provider evidence");
  if (Array.isArray(payload.reasons) && payload.reasons.length) parts.push(payload.reasons.join(" · "));
  if (item?.created_at) {
    const time = new Date(item.created_at);
    if (!Number.isNaN(time.valueOf())) parts.push(time.toLocaleString("en-IN"));
  }
  return parts.join(" · ") || "Persisted incident audit evidence";
}

async function refreshFreeViews() {
  if (!state.incidentId) return;
  try {
    const [detail, control, evaluation] = await Promise.all([
      getIncident(),
      getControl(),
      api("/evaluations/reproducible"),
    ]);
    assertIncidentBinding(detail);
    state.control = control;

    const linked = Array.isArray(detail?.linked_event_ids) ? detail.linked_event_ids.length : 0;
    $("paymentsList").innerHTML = `<div class="list-card"><div><strong>${escapeHTML(String(linked))} linked payment events</strong><span>SIMULATED DEMO DATA · exact sandbox run</span></div><span class="badge sim">SIMULATED</span></div>`;
    $("incidentsList").innerHTML = `<div class="list-card"><div><strong>${escapeHTML(String(detail?.method || "payment").toUpperCase())} payment degradation</strong><span>${escapeHTML(formatRisk(detail?.estimated_amount_at_risk || 0))} estimated at risk</span></div><span class="badge warn">${escapeHTML(detail?.state || "open")}</span></div>`;

    const recovered = Number(control?.actual_recovered_amount_paise || 0);
    $("historyList").innerHTML = recovered > 0 && control?.control_state === "recovered"
      ? `<div class="list-card"><div><strong>${escapeHTML(money(recovered))} recovered</strong><span>Authoritative Razorpay Test Mode Outcome for this incident</span></div><span class="badge ok">PROVIDER VERIFIED</span></div>`
      : `<div class="list-card"><div><strong>₹0 actual recovered</strong><span>Waiting for authoritative provider evidence for this incident.</span></div><span class="badge test">RAZORPAY TEST MODE</span></div>`;

    const audit = Array.isArray(detail?.audit) ? detail.audit : [];
    $("auditList").innerHTML = audit.length
      ? audit.map((item) => `<div class="list-card"><div><strong>${escapeHTML(auditTitle(item.event_type))}</strong><span>${escapeHTML(auditDetail(item))}</span></div></div>`).join("")
      : `<div class="support" style="margin-top:18px">No persisted incident audit events yet.</div>`;

    const adaptive = evaluation?.policies?.adaptive || {};
    const fixed = evaluation?.policies?.fixed || {};
    const seedCount = Number(adaptive?.seed_count || evaluation?.seeds?.length || 0);
    $("evaluationList").innerHTML = `<div class="list-card"><div><strong>Adaptive Sentinel · ${escapeHTML(money(adaptive?.recovered_amount || 0))}</strong><span>${escapeHTML(String(seedCount))} reproducible seeds · Policy violations ${escapeHTML(String(adaptive?.safety_violations ?? "—"))}</span></div><span class="badge sim">SIMULATED</span></div><div class="list-card"><div><strong>Fixed retry · ${escapeHTML(money(fixed?.recovered_amount || 0))}</strong><span>${escapeHTML(String(seedCount))} reproducible seeds · Policy violations ${escapeHTML(String(fixed?.safety_violations ?? "—"))}</span></div><span class="badge sim">SIMULATED</span></div>`;
  } catch (_) {
    // The guided incident remains usable even if a secondary exploration read fails.
  }
}

function unlockExploration() {
  $("recovered").classList.remove("show");
  $("awaiting").classList.remove("show");
  state.freeExplore = true;
  const recovered = Number(state.control?.actual_recovered_amount_paise || 0);
  $("freeBanner").textContent = recovered > 0
    ? `Provider outcome verified · ${money(recovered)} recovered`
    : "Provider evidence pending · free exploration unlocked";
  $("freeBanner").classList.add("show");
  switchView("overview");
  refreshFreeViews();
}

function resetSandbox() {
  $("reviewPanel").classList.remove("show");
  $("awaiting").classList.remove("show");
  $("recovered").classList.remove("show");
  $("investFocus").classList.remove("show");
  $("incidentFocus").classList.remove("show");
  $("dim").classList.remove("show");
  $("openRecovery").classList.add("hidden");
  $("approveAction").disabled = true;
  $("aiCopy").textContent = "Sentinel analyzes bounded evidence before review.";
  $("aiMeta").classList.add("hidden");
  $("policyAllowed").innerHTML = `<div class="policy-box"><div class="copy">Waiting for Policy result.</div></div>`;
  $("policyBlocked").innerHTML = `<div class="policy-box blocked"><div class="copy">Waiting for blocked-action reasons.</div></div>`;
  $("recommendationAction").textContent = "Waiting for backend recommendation";
  $("recommendationReason").textContent = "Ranking occurs only among Policy-permitted actions.";
  $("freeBanner").classList.remove("show");
  $("freeBanner").textContent = "Provider evidence pending · free exploration unlocked";
  state.freeExplore = false;
  switchView("overview");
  seedHealthyUI();
}

$("startSandbox").addEventListener("click", openSandbox);
$("exitSandbox").addEventListener("click", closeSandbox);
$("guideAction").addEventListener("click", async () => {
  try {
    await startMerchantIncident();
  } catch (error) {
    $("guideTitle").textContent = "Incident setup needs attention";
    $("guideCopy").textContent = error.message;
    $("guideAction").disabled = false;
  }
});
$("reviewIncident").addEventListener("click", openReview);
$("keepMonitoring").addEventListener("click", () => { $("incidentFocus").classList.remove("show"); $("dim").classList.remove("show"); });
$("approveAction").addEventListener("click", approveAndExecute);
$("doNotAct").addEventListener("click", () => $("reviewPanel").classList.remove("show"));
$("openRecovery").addEventListener("click", openRecoveryLink);
$("continueExploring").addEventListener("click", unlockExploration);
$("exploreConsole").addEventListener("click", unlockExploration);
$("replaySandbox").addEventListener("click", resetSandbox);
document.querySelectorAll(".nav-item[data-view]").forEach((item) => item.addEventListener("click", () => switchView(item.dataset.view)));

seedHealthyUI();
startStory();
