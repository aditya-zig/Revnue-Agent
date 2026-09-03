const root = document.getElementById("app");

const stylesheet = "/static/css/dashboard-fixes.css";
if (!document.querySelector(`link[href="${stylesheet}"]`)) {
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = stylesheet;
  document.head.appendChild(link);
}

const moneyFormatter = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const dateFormatter = new Intl.DateTimeFormat("en-IN", {
  day: "2-digit",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

const hiddenTechnicalKeys = new Set([
  "raw_hash",
  "signature",
  "signature_hash",
  "raw_body",
]);

const actionLabels = {
  payment_link: "Payment link",
  retry: "Retry payment",
  contact: "Contact customer",
  promise: "Promise to pay",
  escalate: "Escalate",
  stop: "Stop recovery",
};

const eventKindLabels = {
  "raw event": "Provider evidence",
  audit: "Audit trail",
  decision: "Policy decision",
  action: "Recovery action",
  outcome: "Outcome",
};

function labelFor(key) {
  return String(key || "Detail")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function isMoneyKey(key) {
  return /(amount|value_paise|recoverable_impact|revenue|cost)/i.test(key);
}

function isPercentKey(key) {
  return /(rate|probability|confidence)/i.test(key);
}

function isTechnicalKey(key) {
  return /(id$|_id$|reference|version|key$|payment_id|order_id|event_id)/i.test(key);
}

function humanizeToken(value) {
  return String(value)
    .replace(/[._]/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function actionLabel(value) {
  const key = String(value || "").trim().toLowerCase();
  return actionLabels[key] || humanizeToken(value);
}

function formatValue(key, value) {
  if (value === null || value === undefined || value === "") return "Not recorded";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") {
    if (isMoneyKey(key)) return moneyFormatter.format(value / 100);
    if (isPercentKey(key) && Math.abs(value) <= 1) return `${(value * 100).toFixed(1)}%`;
    return new Intl.NumberFormat("en-IN").format(value);
  }
  if (typeof value === "string") {
    if (/^\d{4}-\d{2}-\d{2}T/.test(value)) {
      const date = new Date(value);
      if (!Number.isNaN(date.valueOf())) return dateFormatter.format(date);
    }
    if (!isTechnicalKey(key) && /^[a-z0-9]+(?:[._][a-z0-9]+)+$/i.test(value)) {
      return humanizeToken(value);
    }
  }
  return String(value);
}

function primitiveRow(key, value) {
  const row = document.createElement("div");
  row.className = "readable-detail-item";

  const term = document.createElement("dt");
  term.textContent = labelFor(key);

  const description = document.createElement("dd");
  description.textContent = formatValue(key, value);
  if (isTechnicalKey(key)) description.classList.add("mono");

  row.append(term, description);
  return row;
}

function normalizeObject(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) return input;

  const normalized = {};
  const source = input.payload && typeof input.payload === "object"
    ? { event: input.type, ...input.payload }
    : input;

  Object.entries(source).forEach(([key, value]) => {
    if (hiddenTechnicalKeys.has(key)) return;
    normalized[key] = value;
  });

  if (Object.prototype.hasOwnProperty.call(input, "raw_hash")) {
    normalized.evidence_integrity = "Recorded";
  }

  return normalized;
}

function renderObject(input, depth = 0) {
  const object = normalizeObject(input);
  const wrapper = document.createElement("div");
  wrapper.className = depth ? "readable-nested" : "readable-details";

  const primitiveEntries = [];
  const complexEntries = [];

  Object.entries(object || {}).forEach(([key, value]) => {
    if (value && typeof value === "object") complexEntries.push([key, value]);
    else primitiveEntries.push([key, value]);
  });

  if (primitiveEntries.length) {
    const list = document.createElement("dl");
    list.className = "readable-detail-grid";
    primitiveEntries.forEach(([key, value]) => list.appendChild(primitiveRow(key, value)));
    wrapper.appendChild(list);
  }

  complexEntries.forEach(([key, value]) => {
    const group = document.createElement("section");
    group.className = "readable-detail-group";

    const heading = document.createElement("h5");
    heading.textContent = labelFor(key);
    group.appendChild(heading);

    if (Array.isArray(value)) {
      if (!value.length) {
        const empty = document.createElement("p");
        empty.className = "readable-empty";
        empty.textContent = "None recorded";
        group.appendChild(empty);
      } else if (value.every((item) => item === null || typeof item !== "object")) {
        const list = document.createElement("div");
        list.className = "readable-chip-list";
        value.forEach((item) => {
          const chip = document.createElement("span");
          chip.textContent = formatValue(key, item);
          list.appendChild(chip);
        });
        group.appendChild(list);
      } else {
        const cards = document.createElement("div");
        cards.className = "readable-object-list";
        value.forEach((item, index) => {
          const card = document.createElement("div");
          card.className = "readable-object-card";
          const title = document.createElement("strong");
          title.textContent = `${labelFor(key)} ${index + 1}`;
          card.appendChild(title);
          if (item && typeof item === "object") card.appendChild(renderObject(item, depth + 1));
          else {
            const text = document.createElement("p");
            text.textContent = formatValue(key, item);
            card.appendChild(text);
          }
          cards.appendChild(card);
        });
        group.appendChild(cards);
      }
    } else {
      group.appendChild(renderObject(value, depth + 1));
    }

    wrapper.appendChild(group);
  });

  if (!primitiveEntries.length && !complexEntries.length) {
    const empty = document.createElement("p");
    empty.className = "readable-empty";
    empty.textContent = "No additional details recorded.";
    wrapper.appendChild(empty);
  }

  return wrapper;
}

function replaceRawBlock(pre) {
  if (pre.dataset.readableReplaced === "true") return;
  pre.dataset.readableReplaced = "true";

  let parsed;
  try {
    parsed = JSON.parse(pre.textContent || "{}");
  } catch {
    const note = document.createElement("p");
    note.className = "readable-empty";
    note.textContent = "Technical evidence is recorded but hidden from the normal operator view.";
    pre.replaceWith(note);
    return;
  }

  const section = parsed && typeof parsed === "object"
    ? renderObject(parsed)
    : primitiveRow("Value", parsed);
  pre.replaceWith(section);
}

function cleanTimelineTimestamps() {
  root?.querySelectorAll(".event-time:not([data-readable-time])").forEach((node) => {
    node.dataset.readableTime = "true";
    const parsed = new Date(node.textContent.trim());
    if (!Number.isNaN(parsed.valueOf())) node.textContent = dateFormatter.format(parsed);
  });
}

function cleanEventCopy() {
  root?.querySelectorAll(".event h4 .badge:not([data-readable-copy])").forEach((badge) => {
    badge.dataset.readableCopy = "true";
    const current = badge.textContent.trim().toLowerCase();
    const next = eventKindLabels[current] || humanizeToken(current);
    if (badge.textContent !== next) badge.textContent = next;
  });

  root?.querySelectorAll(".event .case-sub:not([data-readable-copy])").forEach((summary) => {
    summary.dataset.readableCopy = "true";
    const next = summary.textContent
      .split(" · ")
      .map((part) => {
        const value = part.trim();
        if (/^(demo|case|order|finding|pay|evt)_/i.test(value)) return value;
        if (/^[a-z]+(?:[._][a-z]+)+$/i.test(value)) return humanizeToken(value);
        return value;
      })
      .join(" · ");
    if (summary.textContent !== next) summary.textContent = next;
  });
}

function cleanActionCopy() {
  root?.querySelectorAll(".policy-action-tags .badge, .ranked-action-title strong").forEach((node) => {
    if (node.dataset.readableCopy === "true") return;
    node.dataset.readableCopy = "true";
    const next = actionLabel(node.textContent);
    if (node.textContent !== next) node.textContent = next;
  });

  root?.querySelectorAll("button.review").forEach((button) => {
    if (button.dataset.readableCopy === "true") return;
    button.dataset.readableCopy = "true";
    const raw = button.textContent.trim();
    const recommended = /^Approve recommended:\s*/i.test(raw);
    const action = raw.replace(/^Approve recommended:\s*/i, "").replace(/^Approve\s+/i, "");
    const next = recommended
      ? `Approve recommended: ${actionLabel(action)}`
      : `Approve ${actionLabel(action)}`;
    if (button.textContent !== next) button.textContent = next;
  });
}

function cleanFindingReferences() {
  root?.querySelectorAll(".panel-title").forEach((heading) => {
    const value = heading.textContent.trim();
    if (!/^finding_[a-z0-9]+$/i.test(value) || heading.dataset.readableFinding === "true") return;
    heading.dataset.readableFinding = "true";
    heading.title = value;
    heading.textContent = "Top recovery finding";

    const reference = document.createElement("span");
    reference.className = "technical-ref";
    const shortId = value.length > 20 ? `${value.slice(0, 12)}…${value.slice(-6)}` : value;
    reference.textContent = `Reference ${shortId}`;
    heading.insertAdjacentElement("afterend", reference);
  });

  root?.querySelectorAll(".hybrid-impact .hybrid-note").forEach((note) => {
    if (note.dataset.readableFinding === "true") return;
    note.dataset.readableFinding = "true";
    const next = note.textContent.replace(/^Finding\s+\S+\s+·\s*/i, "");
    if (note.textContent !== next) note.textContent = next;
  });
}

function replaceRawDetails() {
  root?.querySelectorAll(".event pre, pre.json").forEach(replaceRawBlock);
}

let scheduled = false;
function cleanUi() {
  scheduled = false;
  replaceRawDetails();
  cleanTimelineTimestamps();
  cleanEventCopy();
  cleanActionCopy();
  cleanFindingReferences();
}

function scheduleCleanUi() {
  if (scheduled) return;
  scheduled = true;
  window.requestAnimationFrame(cleanUi);
}

if (root) {
  cleanUi();
  const observer = new MutationObserver(scheduleCleanUi);
  observer.observe(root, { childList: true, subtree: true });
}
