const EXACT_INR = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  minimumFractionDigits: 0,
  maximumFractionDigits: 2,
});

function trim(value) {
  if (value >= 100) return value.toFixed(0);
  if (value >= 10) return value.toFixed(1).replace(/\.0$/, "");
  return value.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
}

export function formatMoney(value, { exact = false } = {}) {
  const paise = Number(value ?? 0);
  const rupees = Number.isFinite(paise) ? paise / 100 : 0;
  if (exact) return EXACT_INR.format(rupees);

  const abs = Math.abs(rupees);
  const sign = rupees < 0 ? "-" : "";
  if (abs >= 10_000_000) return `${sign}₹${trim(abs / 10_000_000)}Cr`;
  if (abs >= 100_000) return `${sign}₹${trim(abs / 100_000)}L`;
  if (abs >= 1_000) return `${sign}₹${trim(abs / 1_000)}K`;
  return `${sign}₹${new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 }).format(abs)}`;
}

export function claimTagForSource(source) {
  if (source === "razorpay_test") return "TEST MODE";
  if (source === "mock") return "MOCK";
  if (String(source || "").startsWith("simulated") || source === "csv_import") return "SIMULATED";
  return "";
}

export function claimTagForSources(sources) {
  const uniqueSources = [...new Set(sources || [])];
  return uniqueSources.length === 1 ? claimTagForSource(uniqueSources[0]) : "";
}

export function findOutcomeForCase(data, caseId) {
  const timeline = data?.timeline?.find((item) => item.case_id === caseId);
  return timeline?.events?.find((event) => event.kind === "outcome")?.data || null;
}
