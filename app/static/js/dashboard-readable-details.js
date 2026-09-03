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
  maximumFractionDigits: 2,
});

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
  return /(id$|_id$|reference|hash|version|key$|payment_id|order_id|event_id)/i.test(key);
}

function humanizeEnum(value) {
  return String(value)
    .replace(/_/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
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
      if (!Number.isNaN(date.valueOf())) return date.toLocaleString("en-IN");
    }
    if (!isTechnicalKey(key) && /^[a-z0-9]+(?:_[a-z0-9]+)+$/i.test(value)) {
      return humanizeEnum(value);
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

function renderObject(object, depth = 0) {
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
    note.textContent = "Technical details are recorded but are not shown as raw code.";
    pre.replaceWith(note);
    return;
  }

  const section = parsed && typeof parsed === "object"
    ? renderObject(parsed)
    : primitiveRow("Value", parsed);
  pre.replaceWith(section);
}

function replaceRawDetails() {
  if (!root) return;
  root.querySelectorAll(".event pre, pre.json").forEach(replaceRawBlock);
}

if (root) {
  replaceRawDetails();
  const observer = new MutationObserver(() => replaceRawDetails());
  observer.observe(root, { childList: true, subtree: true });
}
