import { ApiError, getDashboard } from "./api.js";

const VIEW_IDS = [
  "overview",
  "queue",
  "detail",
  "exceptions",
  "settings",
  "investigation",
  "evaluation",
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

function formatMoney(value) {
  const amount = Number(value || 0) / 100;
  return `INR ${amount.toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
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
    "MOCK",
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

function closeZoom() {
  if (!state.zoomed) return;
  state.zoomed.classList.remove("zoomed");
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
