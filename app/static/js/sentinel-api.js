import { requestJSON } from "./api.js";

export function listIncidents(options = {}) {
  return requestJSON("/incidents", options);
}

export function getIncident(incidentId, options = {}) {
  return requestJSON(`/incidents/${encodeURIComponent(incidentId)}`, options);
}

export function getIncidentControl(incidentId, options = {}) {
  return requestJSON(`/incidents/${encodeURIComponent(incidentId)}/control`, options);
}

export function investigateIncident(incidentId, idempotencyKey, options = {}) {
  return requestJSON(`/incidents/${encodeURIComponent(incidentId)}/investigate`, {
    ...options,
    method: "POST",
    body: { idempotency_key: idempotencyKey },
  });
}

export function approveIncident(incidentId, options = {}) {
  return requestJSON(`/incidents/${encodeURIComponent(incidentId)}/approve`, {
    ...options,
    method: "POST",
  });
}

export function executeIncident(incidentId, options = {}) {
  return requestJSON(`/incidents/${encodeURIComponent(incidentId)}/execute`, {
    ...options,
    method: "POST",
  });
}

export function getReplayStatus(options = {}) {
  return requestJSON("/replay/status", options);
}

export function runReplay(options = {}) {
  return requestJSON("/replay/run?scenario=primary", {
    ...options,
    method: "POST",
  });
}
