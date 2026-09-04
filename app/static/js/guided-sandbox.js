const $ = (id) => document.getElementById(id);
const money = (paise) => new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(Number(paise || 0) / 100);
const pct = (value) => `${(Number(value || 0) * 100).toFixed(1)}%`;
const sleep = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));

const state = {
  incident: null,
  control: null,
  providerLink: null,
  baselineRecovered: 0,
  purchaseOpened: false,
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

function escapeHTML(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
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
  $("sandboxTrend").setAttribute("d", "M0 111 C80 103 135 111 190 101 S310 111 365 102 S475 108 535 99 S665 111 730 101 S840 103 900 94");
  $("activityRows").innerHTML = "";
  $("eventLog").innerHTML = "";
  addActivity("₹899", "UPI", "Captured");
  addActivity("₹2,499", "UPI", "Captured");
  addActivity("₹1,299", "Card", "Captured");
  addActivity("₹749", "UPI", "Captured");
  addEvent("Sentinel monitoring", "Payment health is within the merchant baseline.");
  $("guideTitle").textContent = "Start with a test purchase";
  $("guideCopy").textContent = "Open the customer storefront and make one UPI payment. Sentinel will watch what happens next.";
  $("guideAction").textContent = "Open test storefront";
  state.purchaseOpened = false;
  state.incident = null;
  state.control = null;
  state.providerLink = null;
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
    $("storyStatus").textContent = "Sentinel monitoring";
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
      $("storyStatus").textContent = "Investigating incident…";
      trend.setAttribute("d", "M0 88 C100 82 180 84 270 82 S390 88 470 83 C535 84 570 110 610 150 S660 184 700 191");
      $("storyFeed").insertAdjacentHTML("afterbegin", `<div class="row"><div><strong>₹2,499</strong><small>UPI</small></div><span class="pill fail">Failed</span></div>`);
    });
    later(5600, () => {
      card.innerHTML = `<div class="eyebrow">PAYMENT INCIDENT</div><h3>UPI payment degradation detected</h3><div class="amount">₹46.2K</div><div class="story-copy">ESTIMATED AT RISK · 91.8% baseline → 58.3% current</div>`;
      overlay.classList.add("show");
    });
    later(7900, () => {
      card.innerHTML = `<div class="eyebrow">POLICY &amp; SAFETY</div><h3>Unsafe actions are removed first.</h3><div class="story-policy"><div><span>Send alternate payment link</span><span class="badge ok">ALLOWED</span></div><div class="blocked"><span>Retry hard-decline cases</span><span class="badge block">BLOCKED</span></div></div>`;
    });
    later(10100, () => {
      card.innerHTML = `<div class="eyebrow">PROVIDER VERIFIED</div><div class="amount" style="color:#078c4d">₹2,499</div><h3 style="color:#078c4d">RECOVERED</h3><div class="truth"><span class="badge test">RAZORPAY TEST MODE</span><span class="badge ok">Provider outcome verified</span></div>`;
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

async function getDashboard() {
  return api("/dashboard");
}

async function listIncidents() {
  return api("/incidents");
}

async function findIncident() {
  for (let attempt = 0; attempt < 5; attempt += 1) {
    const incidents = await listIncidents();
    if (Array.isArray(incidents) && incidents.length) return incidents[0];
    await sleep(650);
  }
  return null;
}

function applyIncident(incident) {
  state.incident = incident;
  const baseline = Number(incident?.baseline_metrics?.success_rate ?? .918);
  const current = Number(incident?.observed_metrics?.success_rate ?? .583);
  const affected = Number(incident?.affected_attempt_count ?? incident?.failed_attempt_count ?? 37);
  const risk = Number(incident?.peak_estimated_amount_at_risk_paise ?? incident?.estimated_amount_at_risk ?? 4622500);
  $("healthTitle").textContent = "Unusual UPI degradation detected";
  $("healthSupport").textContent = "Success rate moved outside the merchant's normal range.";
  $("successRate").textContent = pct(current);
  $("baselineRate").textContent = pct(baseline);
  $("affectedCount").textContent = String(affected);
  $("healthBadge").textContent = "Investigating incident…";
  $("healthBadge").className = "badge warn";
  $("sandboxTrend").setAttribute("d", "M0 111 C110 104 210 106 315 102 S500 108 570 109 C645 112 690 160 735 205 S830 250 900 260");
  $("incidentRisk").textContent = formatRisk(risk);
  $("incidentCompare").textContent = `${pct(baseline)} baseline → ${pct(current)} current · ${affected} payments affected`;
  $("reviewRisk").textContent = `${formatRisk(risk)} estimated at risk`;
  $("factBaseline").textContent = pct(baseline);
  $("factCurrent").textContent = pct(current);
  $("factAffected").textContent = String(affected);
  $("factsCopy").textContent = `Success rate dropped below the merchant baseline. The affected cohort is concentrated in UPI attempts. ${affected} payment attempts are linked to this incident window. ${money(risk)} is estimated exposure, not recovered revenue.`;
}

async function startMerchantIncident() {
  $("guideAction").disabled = true;
  $("guideTitle").textContent = "Sentinel is watching";
  $("guideCopy").textContent = "The merchant-day replay is adding surrounding payment activity so Sentinel can detect whether this is an incident.";
  addEvent("Test purchase observed", "Sentinel is evaluating the surrounding merchant payment window.");
  addActivity("₹2,499", "UPI", "Failed");
  try {
    const dashboard = await getDashboard();
    state.baselineRecovered = Number(dashboard?.executive?.test_mode_value || 0);
  } catch (_) {
    state.baselineRecovered = 0;
  }
  try {
    await api("/replay/start", { method: "POST" });
  } catch (error) {
    if (error.status !== 409) throw error;
  }
  const incident = await findIncident();
  if (!incident) throw new Error("Sentinel did not create an incident from the replay.");
  applyIncident(incident);
  addActivity("₹1,299", "UPI", "Failed");
  addActivity("₹749", "UPI", "Failed");
  addEvent("UPI degradation detected", "The current success rate moved outside the normal range.");
  await showInvestigation();
  $("guideAction").disabled = false;
}

async function showInvestigation() {
  $("dim").classList.add("show");
  $("investFocus").classList.add("show");
  const steps = [...document.querySelectorAll("#investSteps .invest-step")];
  for (const step of steps) step.classList.remove("done");
  for (let index = 0; index < steps.length; index += 1) {
    await sleep(620);
    steps[index].classList.add("done");
  }
  $("investFooter").textContent = "Incident ready for review";
  await sleep(650);
  $("investFocus").classList.remove("show");
  $("incidentFocus").classList.add("show");
}

async function openReview() {
  $("incidentFocus").classList.remove("show");
  $("dim").classList.remove("show");
  if (state.incident?.incident_id) {
    try {
      const detail = await api(`/incidents/${encodeURIComponent(state.incident.incident_id)}`);
      applyIncident({ ...state.incident, ...detail });
    } catch (_) {
      // The summary remains sufficient for review if detail refresh is unavailable.
    }
  }
  $("reviewPanel").classList.add("show");
}

function analysisText(control) {
  const analysis = control?.analysis || control?.incident_analysis || null;
  if (!analysis) return { text: "Sentinel completed bounded incident analysis. Root cause remains unconfirmed.", meta: "Deterministic fallback may be in use." };
  const result = analysis.result || analysis.output || analysis;
  const hypotheses = result?.hypotheses;
  let text = "Sentinel completed bounded incident analysis. Root cause remains unconfirmed.";
  if (Array.isArray(hypotheses) && hypotheses.length) {
    const first = hypotheses[0];
    text = typeof first === "string" ? first : first?.statement || text;
  }
  const metadata = analysis.provider_metadata || result?.model_metadata || analysis.model_metadata || {};
  const fallback = Boolean(metadata.fallback_used ?? result?.fallback_used);
  const model = metadata.resolved_model || metadata.requested_model || "OpenRouter model";
  return {
    text,
    meta: fallback ? "Deterministic fallback used; no external model claim is shown as fact." : `Advisory analysis generated by ${model}.`,
  };
}

async function ensureInvestigated() {
  if (!state.incident?.incident_id) throw new Error("No incident is selected.");
  if (state.control?.recommendation || state.control?.analysis) return state.control;
  const key = `guided-${state.incident.incident_id}-${Date.now()}`;
  state.control = await api(`/incidents/${encodeURIComponent(state.incident.incident_id)}/investigate`, {
    method: "POST",
    body: { idempotency_key: key },
  });
  return state.control;
}

async function generateAnalysis() {
  $("generateAnalysis").disabled = true;
  $("aiLoading").classList.remove("hidden");
  try {
    const control = await ensureInvestigated();
    const copy = analysisText(control);
    $("aiCopy").textContent = copy.text;
    $("aiMeta").textContent = copy.meta;
    $("aiMeta").classList.remove("hidden");
    $("generateAnalysis").textContent = "Analysis generated";
  } catch (error) {
    $("aiCopy").textContent = "Advisory analysis could not be generated. Deterministic facts and Policy remain available.";
    $("aiMeta").textContent = error.message;
    $("aiMeta").classList.remove("hidden");
    $("generateAnalysis").disabled = false;
  } finally {
    $("aiLoading").classList.add("hidden");
  }
}

async function approveAndExecute() {
  $("approveAction").disabled = true;
  try {
    await ensureInvestigated();
    await api(`/incidents/${encodeURIComponent(state.incident.incident_id)}/approve`, { method: "POST" });
    $("reviewPanel").classList.remove("show");
    $("awaiting").classList.add("show");
    $("awaitTitle").textContent = "Creating recovery action…";
    $("awaitCopy").textContent = "Approval is not recovery.";
    $("awaitAmount").textContent = "₹0";
    const result = await api(`/incidents/${encodeURIComponent(state.incident.incident_id)}/execute`, { method: "POST" });
    state.providerLink = typeof result?.provider_reference === "string" ? result.provider_reference : null;
    $("awaitTitle").textContent = state.providerLink ? "Payment link created" : "Recovery action created";
    $("awaitCopy").textContent = "Waiting for provider outcome. Approval is not recovery.";
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
  $("awaitTitle").textContent = "Waiting for provider outcome";
  $("awaitCopy").textContent = "Sentinel will count recovery only after authoritative provider evidence arrives.";
}

function startOutcomePolling() {
  stopOutcomePolling();
  const check = async () => {
    try {
      const dashboard = await getDashboard();
      const recovered = Number(dashboard?.executive?.test_mode_value || 0);
      $("actualRecovered").textContent = money(recovered);
      if (recovered > state.baselineRecovered) {
        providerVerified(recovered - state.baselineRecovered);
        return;
      }
    } catch (_) {
      // Keep waiting; provider outcome polling is intentionally best-effort.
    }
    state.polling = window.setTimeout(check, 2200);
  };
  check();
}

function stopOutcomePolling() {
  if (state.polling) window.clearTimeout(state.polling);
  state.polling = null;
}

function providerVerified(delta) {
  stopOutcomePolling();
  const amount = delta > 0 ? delta : 249900;
  $("recoveredAmount").textContent = money(amount);
  $("actualRecovered").textContent = money(state.baselineRecovered + amount);
  $("recovered").classList.add("show");
  addEvent("Provider outcome verified", `${money(amount)} recovered in Razorpay Test Mode.`);
}

function switchView(name) {
  const mapping = { overview: "overviewView", payments: "paymentsView", incidents: "incidentsView", history: "historyView", settings: "settingsView" };
  Object.values(mapping).forEach((id) => $(id).classList.add("hidden"));
  $(mapping[name] || mapping.overview).classList.remove("hidden");
  document.querySelectorAll(".nav-item[data-view]").forEach((item) => item.classList.toggle("active", item.dataset.view === name));
  if (state.freeExplore) refreshFreeViews();
}

async function refreshFreeViews() {
  try {
    const [dashboard, incidents] = await Promise.all([getDashboard(), listIncidents()]);
    const rows = [];
    for (const timeline of dashboard?.timeline || []) {
      for (const event of timeline.events || []) {
        if (event.kind === "raw event" && event.data) rows.push(event.data);
      }
    }
    $("paymentsList").innerHTML = rows.slice(-12).reverse().map((row) => `<div class="list-card"><div><strong>${escapeHTML(money(row.amount))} · ${escapeHTML((row.method || "payment").toUpperCase())}</strong><span>${escapeHTML(row.provider || "merchant evidence")}</span></div><span class="badge ${row.status === "captured" ? "ok" : "block"}">${escapeHTML(row.status || "unknown")}</span></div>`).join("") || `<div class="support" style="margin-top:18px">No persisted payment rows yet.</div>`;
    $("incidentsList").innerHTML = (incidents || []).map((incident) => `<div class="list-card"><div><strong>${escapeHTML((incident.method || "payment").toUpperCase())} payment degradation</strong><span>${escapeHTML(formatRisk(incident.estimated_amount_at_risk))} estimated at risk</span></div><span class="badge warn">${escapeHTML(incident.state || "open")}</span></div>`).join("") || `<div class="support" style="margin-top:18px">No active incidents.</div>`;
    const recovered = Number(dashboard?.executive?.test_mode_value || 0);
    $("historyList").innerHTML = recovered > 0 ? `<div class="list-card"><div><strong>${escapeHTML(money(recovered))} recovered</strong><span>Authoritative provider-backed Test Mode Outcome</span></div><span class="badge ok">PROVIDER VERIFIED</span></div>` : `<div class="support" style="margin-top:18px">No provider-verified recovery outcome yet.</div>`;
  } catch (_) {
    // Free exploration remains usable even if a refresh request fails.
  }
}

function unlockExploration() {
  $("recovered").classList.remove("show");
  $("awaiting").classList.remove("show");
  state.freeExplore = true;
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
  $("generateAnalysis").textContent = "Generate analysis";
  $("generateAnalysis").disabled = false;
  $("approveAction").disabled = false;
  $("aiCopy").textContent = "Generate an advisory hypothesis from the bounded incident evidence.";
  $("aiMeta").classList.add("hidden");
  $("freeBanner").classList.remove("show");
  state.freeExplore = false;
  switchView("overview");
  seedHealthyUI();
}

$("startSandbox").addEventListener("click", openSandbox);
$("exitSandbox").addEventListener("click", closeSandbox);
$("guideAction").addEventListener("click", async () => {
  if (!state.purchaseOpened) {
    window.open("/storefront", "reroute-storefront", "noopener,noreferrer");
    state.purchaseOpened = true;
    $("guideTitle").textContent = "Make the test purchase";
    $("guideCopy").textContent = "Use Razorpay Test Mode in the storefront. When you return, continue so Sentinel can evaluate the merchant payment window.";
    $("guideAction").textContent = "Continue after test purchase";
    addEvent("Customer storefront opened", "Waiting for the Test Mode purchase before incident evaluation.");
    return;
  }
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
$("generateAnalysis").addEventListener("click", generateAnalysis);
$("approveAction").addEventListener("click", approveAndExecute);
$("doNotAct").addEventListener("click", () => $("reviewPanel").classList.remove("show"));
$("openRecovery").addEventListener("click", openRecoveryLink);
$("exploreConsole").addEventListener("click", unlockExploration);
$("replaySandbox").addEventListener("click", resetSandbox);
document.querySelectorAll(".nav-item[data-view]").forEach((item) => item.addEventListener("click", () => switchView(item.dataset.view)));

seedHealthyUI();
startStory();