const API_ROOT = "/api/v1";

export class ApiError extends Error {
  constructor(message, { status = 0, detail = null } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

function detailMessage(detail, fallback) {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (typeof item === "string" ? item : item?.msg))
      .filter(Boolean);
    if (messages.length) return messages.join(", ");
  }
  return fallback;
}

async function parseResponse(response) {
  if (response.status === 204) return null;
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) return response.json();
  return response.text();
}

export async function requestJSON(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("Accept", "application/json");
  let body = options.body;
  if (body !== undefined && body !== null && typeof body !== "string") {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(body);
  }

  const response = await fetch(`${API_ROOT}${path}`, {
    ...options,
    body,
    headers,
    credentials: "same-origin",
  });
  const payload = await parseResponse(response);
  if (!response.ok) {
    const detail = payload && typeof payload === "object" ? payload.detail : payload;
    throw new ApiError(
      detailMessage(detail, `Request failed with status ${response.status}`),
      { status: response.status, detail },
    );
  }
  return payload;
}

export function getDashboard(options = {}) {
  return requestJSON("/dashboard", options);
}

export function listCases(options = {}) {
  return requestJSON("/cases", options);
}

export function getAudit(caseId, options = {}) {
  return requestJSON(`/audit/${encodeURIComponent(caseId)}`, options);
}

export function getCasePolicy(caseId, options = {}) {
  return requestJSON(`/cases/${encodeURIComponent(caseId)}/policy`, options);
}

export function getRankedActions(caseId, options = {}) {
  return requestJSON(`/cases/${encodeURIComponent(caseId)}/ranked-actions`, options);
}

export function createAction(caseId, payload, options = {}) {
  return requestJSON(`/cases/${encodeURIComponent(caseId)}/actions`, {
    ...options,
    method: "POST",
    body: payload,
  });
}

export function createDecision(caseId, payload, options = {}) {
  return requestJSON(`/cases/${encodeURIComponent(caseId)}/decisions`, {
    ...options,
    method: "POST",
    body: payload,
  });
}

export function listPaymentExceptions(options = {}) {
  return requestJSON("/exceptions", options);
}

export function openPaymentException(caseId, payload, options = {}) {
  return requestJSON(`/cases/${encodeURIComponent(caseId)}/exceptions`, {
    ...options,
    method: "POST",
    body: payload,
  });
}

export function resolvePaymentException(exceptionId, payload, options = {}) {
  return requestJSON(`/exceptions/${encodeURIComponent(exceptionId)}/resolve`, {
    ...options,
    method: "POST",
    body: payload,
  });
}

export function getPolicySettings(options = {}) {
  return requestJSON("/policy-settings", options);
}

export function updatePolicySettings(payload, options = {}) {
  return requestJSON("/policy-settings", {
    ...options,
    method: "PUT",
    body: payload,
  });
}

export function replyToMockMessage(providerReference, payload, options = {}) {
  return requestJSON(`/mock-inbox/${encodeURIComponent(providerReference)}/reply`, {
    ...options,
    method: "POST",
    body: payload,
  });
}

export function listFindings(options = {}) {
  return requestJSON("/findings", options);
}

export function detectFindings(options = {}) {
  return requestJSON("/findings/detect", {
    ...options,
    method: "POST",
  });
}

export function getFinding(findingId, options = {}) {
  return requestJSON(`/findings/${encodeURIComponent(findingId)}`, options);
}

export function getPublishedEvaluation(options = {}) {
  return requestJSON("/evaluations/published", options);
}

export function getReproducibleEvaluation(options = {}) {
  return requestJSON("/evaluations/reproducible", options);
}

export function getRecoveryModel(options = {}) {
  return requestJSON("/evaluations/recovery-model", options);
}
