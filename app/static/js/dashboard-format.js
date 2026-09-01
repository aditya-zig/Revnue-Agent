const INR_FORMAT = new Intl.NumberFormat("en-IN", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function formatMoney(value) {
  const paise = Number(value ?? 0);
  const rupees = Number.isFinite(paise) ? paise / 100 : 0;
  return `₹INR ${INR_FORMAT.format(rupees)}`;
}

export function claimTagForSource(source) {
  if (source === "razorpay_test") return "TEST MODE";
  if (source === "mock") return "MOCK";
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
