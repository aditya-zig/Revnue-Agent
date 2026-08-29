from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select

from app.api.evaluations import get_published_evaluation
from app.db.tables import (
    ActionEvent,
    AuditEvent,
    Decision,
    LeakFinding,
    Outcome,
    PaymentEvent,
    PaymentException,
    RecoveryCase,
)
from app.leak_analysis import finding_sort_key
from app.policy import evaluate_policy, get_policy_configuration

router = APIRouter(tags=["dashboard"])


def _worklist_sort_key(item: dict) -> tuple[int, int, str]:
    # ADR 0006 order: escalations -> aged PaymentExceptions -> eligible by expected net value -> investigated by age
    state = item["state"]
    if state == "escalated":
        rank = 0
        # aged: older opened_at first
        secondary = item.get("opened_at") or ""
        return (rank, 0, secondary)
    if item["open_payment_exception_at"]:
        return (1, 0, item["open_payment_exception_at"])
    if state == "eligible":
        rank = 2
        # eligible by expected net value descending (negate for ascending sort), then age
        ev = item.get("expected_value")
        # use negative for descending; None -> lowest priority
        neg_ev = -(ev or -1_000_000_000)
        return (rank, neg_ev, item.get("opened_at") or "")
    if state == "investigated":
        rank = 3
        return (rank, 0, item.get("opened_at") or "")
    # others like detected
    return (4, 0, item.get("opened_at") or "")


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard_page() -> str:
    return DASHBOARD_HTML


@router.get("/api/v1/dashboard")
def get_dashboard(request: Request) -> dict:
    with request.app.state.session_factory() as session:
        configuration = get_policy_configuration(session, request.app.state)
        findings = session.scalars(select(LeakFinding)).all()
        findings.sort(key=finding_sort_key)
        cases = session.scalars(select(RecoveryCase).order_by(RecoveryCase.case_id)).all()
        exceptions = session.scalars(
            select(PaymentException).order_by(PaymentException.opened_at.desc())
        ).all()
        open_exceptions_by_case = {
            exception.case_id: exception.opened_at.isoformat()
            for exception in exceptions
            if exception.state == "open"
        }
        worklist = [
            _case_summary(
                session,
                case,
                request,
                configuration,
                open_exceptions_by_case.get(case.case_id),
            )
            for case in cases
        ]
        # Worklist ordering follows ADR 0006.
        worklist.sort(key=lambda item: _worklist_sort_key(item))
        # Executive measures per ADR 0006: one ClaimTag per figure
        # Revenue at Risk = sum amount_at_risk where state not recovered
        # Estimated Recoverable = single top LeakFinding recoverable_impact (ESTIMATED)
        # Actual Recovered = sum Outcome where source=razorpay_test (TEST MODE)
        # Simulated Recovery is in evaluation (SIMULATED) - not computed here
        revenue_at_risk = sum(
            case.amount_at_risk for case in cases if case.state not in {"recovered", "stopped"}
        )
        top_recoverable = findings[0].recoverable_impact if findings else 0
        estimated_value = top_recoverable
        outcomes = session.scalars(select(Outcome)).all()
        test_mode_value = sum(
            outcome.recovered_amount for outcome in outcomes if outcome.source == "razorpay_test"
        )
        # fallback: if no razorpay_test outcomes, keep 0 rather than summing all (preserve ClaimTag discipline)

        return {
            "executive": {
                "top_leak": _finding(findings[0]) if findings else None,
                "revenue_at_risk": revenue_at_risk,
                "estimated_value": estimated_value,
                "test_mode_value": test_mode_value,
                "open_cases": sum(
                    case["state"] not in {"recovered", "closed", "stopped"} for case in worklist
                ),
            },
            "investigation": _finding(findings[0]) if findings else None,
            "worklist": worklist,
            "timeline": [_timeline(session, case) for case in cases],
            "payment_exceptions": [_payment_exception(exception) for exception in exceptions],
            "policy_settings": _policy_settings(configuration),
            "evaluation": get_published_evaluation(),
            "mock_inbox": [
                _action(action)
                for action in session.scalars(
                    select(ActionEvent)
                    .where(ActionEvent.tool.in_(["contact", "promise"]))
                    .order_by(ActionEvent.executed_at.desc())
                )
            ],
        }


def _case_summary(
    session, case: RecoveryCase, request: Request, configuration, open_exception_at: str | None
) -> dict:
    if getattr(case, "obligation_reference", None):
        payment = session.scalar(
            select(PaymentEvent)
            .where(PaymentEvent.obligation_reference == case.obligation_reference)
            .order_by(PaymentEvent.occurred_at.desc())
        )
    else:
        payment = session.scalar(
            select(PaymentEvent)
            .where(PaymentEvent.payment_id == case.payment_id)
            .order_by(PaymentEvent.occurred_at.desc())
        )
    decision = session.scalar(
        select(Decision)
        .where(Decision.case_id == case.case_id)
        .order_by(Decision.decision_id.desc())
    )
    action = session.scalar(
        select(ActionEvent)
        .where(ActionEvent.case_id == case.case_id)
        .order_by(ActionEvent.executed_at.desc())
    )
    policy = evaluate_policy(
        session,
        case,
        request.app.state.policy_now(),
        configuration.quiet_hours_start,
        configuration.quiet_hours_end,
        configuration.kill_switch,
        configuration.contact_limit,
        configuration.policy_version,
    )
    # expected net value for worklist sorting: prefer persisted Decision, else rank eligible via RecoveryModel
    from app.db.tables import Customer

    customer = session.get(Customer, case.customer_id) if case.customer_id else None
    if decision is not None:
        expected_value = decision.expected_value
    elif case.state == "eligible":
        ranked = request.app.state.recovery_model.rank(case, customer, policy.allowed_actions)
        expected_value = int(ranked[0]["expected_net_value"]) if ranked else None
    else:
        expected_value = None
    contact_count = session.scalar(
        select(func.count()).select_from(ActionEvent).where(
            ActionEvent.case_id == case.case_id,
            ActionEvent.tool.in_(["contact", "promise"]),
        )
    )
    return {
        "case_id": case.case_id,
        "payment_id": case.payment_id,
        "obligation_reference": getattr(case, "obligation_reference", None),
        "customer_id": case.customer_id,
        "amount_at_risk": case.amount_at_risk,
        "state": case.state,
        "opened_at": case.opened_at.isoformat() if case.opened_at else None,
        "evidence": _payment(payment) if payment else None,
        "selected_action": (
            decision.selected_action if decision else action.tool if action else None
        ),
        "expected_value": expected_value,
        "policy": policy.model_dump(),
        "open_payment_exception": open_exception_at is not None,
        "open_payment_exception_at": open_exception_at,
        "contact_budget": max(0, configuration.contact_limit - (contact_count or 0)),
        "owner": "business_owner" if case.state == "escalated" else "operations_worker",
        "blocked_reasons": policy.blocked_reasons,
        "customer": (
            {
                "consent": customer.consent,
                "tenure_days": customer.tenure_days,
                "successful_payments": customer.successful_payments,
                "prior_failures": customer.prior_failures,
            }
            if customer
            else None
        ),
        "ranked_actions": (
            request.app.state.recovery_model.rank(case, customer, policy.allowed_actions)
            if case.state == "eligible"
            else []
        ),
        "human_review": {
            "allowed_actions": policy.allowed_actions,
            "can_execute": case.state == "eligible" and bool(policy.allowed_actions),
        },
    }


def _timeline(session, case: RecoveryCase) -> dict:
    if getattr(case, "obligation_reference", None):
        payment_events = session.scalars(
            select(PaymentEvent)
            .where(PaymentEvent.obligation_reference == case.obligation_reference)
            .order_by(PaymentEvent.occurred_at)
        ).all()
    else:
        payment_events = session.scalars(
            select(PaymentEvent)
            .where(PaymentEvent.payment_id == case.payment_id)
            .order_by(PaymentEvent.occurred_at)
        ).all()
    audit_events = session.scalars(
        select(AuditEvent).where(AuditEvent.case_id == case.case_id).order_by(AuditEvent.created_at)
    ).all()
    decisions = session.scalars(
        select(Decision).where(Decision.case_id == case.case_id).order_by(Decision.decision_id)
    ).all()
    actions = session.scalars(
        select(ActionEvent)
        .where(ActionEvent.case_id == case.case_id)
        .order_by(ActionEvent.executed_at)
    ).all()
    outcome = session.scalar(select(Outcome).where(Outcome.case_id == case.case_id))
    events = [
        {"kind": "raw event", "at": _time(event.occurred_at), "data": _payment(event)}
        for event in payment_events
    ]
    events += [
        {
            "kind": "decision",
            "at": _time(decision.created_at),
            "data": {
                "selected_action": decision.selected_action,
                "expected_value": decision.expected_value,
                "policy_version": decision.policy_version,
                "model_version": decision.model_version,
                "evidence": decision.reason_json.get("evidence", {}),
            },
        }
        for decision in decisions
    ]
    events += [
        {"kind": "action", "at": _time(action.executed_at), "data": _action(action)}
        for action in actions
    ]
    events += [
        {
            "kind": "audit",
            "at": _time(event.created_at),
            "data": {"type": event.event_type, "payload": event.payload},
        }
        for event in audit_events
    ]
    if outcome:
        events.append(
            {
                "kind": "outcome",
                "at": _time(outcome.resolved_at),
                "data": {
                    "recovered": outcome.recovered,
                    "recovered_amount": outcome.recovered_amount,
                    "source": outcome.source,
                },
            }
        )
    events.sort(key=lambda event: (event["at"] is None, event["at"] or ""))
    return {"case_id": case.case_id, "events": events}


def _finding(finding: LeakFinding) -> dict:
    return {
        "finding_id": finding.finding_id,
        "cohort_filter": finding.cohort_filter,
        "recoverable_impact": finding.recoverable_impact,
        "confidence": finding.confidence,
        "evidence": finding.evidence_json,
    }


def _payment(payment: PaymentEvent) -> dict:
    return {
        "event_id": payment.event_id,
        "event_type": payment.event_type,
        "status": payment.status,
        "amount": payment.amount,
        "method": payment.method,
        "error_reason": payment.error_reason,
        "obligation_reference": getattr(payment, "obligation_reference", None),
        "payment_id": payment.payment_id,
        "provider_event_id": payment.provider_event_id,
        "raw_hash": payment.raw_hash,
    }


def _action(action: ActionEvent) -> dict:
    return {
        "case_id": action.case_id,
        "tool": action.tool,
        "status": action.status,
        "provider_reference": action.provider_reference,
        "idempotency_key": action.idempotency_key,
        "reply": action.reply,
        "executed_at": _time(action.executed_at),
    }


def _payment_exception(exception: PaymentException) -> dict:
    return {
        "exception_id": exception.exception_id,
        "case_id": exception.case_id,
        "kind": exception.kind,
        "state": exception.state,
        "evidence": exception.evidence_json,
        "resolution": exception.resolution,
    }


def _policy_settings(configuration) -> dict:
    return {
        "version": configuration.version,
        "policy_version": configuration.policy_version,
        "quiet_hours_start": configuration.quiet_hours_start,
        "quiet_hours_end": configuration.quiet_hours_end,
        "contact_limit": configuration.contact_limit,
        "kill_switch": configuration.kill_switch,
        "mock_identity": configuration.mock_identity,
    }


def _time(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


# ruff: noqa: E501
# ReRoute shell — Razorpay design system
# Worker contract: the 7 tabs and their content containers are stable.
#   Tabs: overview, queue, detail, exceptions, settings, investigation, evaluation
#   Containers: #overview-content, #queue-content, #detail-content, #inbox-content,
#               #exceptions-content, #settings-content, #investigation-content, #evaluation-content
#   JS helpers (money, html, json, tag, render) and the /api/v1/dashboard contract are preserved.
#   New CSS primitives for parallel workers (documented in app/static/css/shell.css):
#     .rzp-card, .rzp-badge, .rzp-btn, .rzp-table-wrap, .rzp-empty, .rzp-skeleton, .rzp-grid, etc.
#   Legacy .card / .tag / .grid / button markup is auto-styled by shell.css for back-compat.
DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light">
<title>ReRoute Intelligence — Razorpay Recovery Operations</title>
<meta name="description" content="ReRoute Intelligence recovery operations dashboard — Razorpay Test Mode">
<link rel="icon" href="/static/img/favicon.png" type="image/png">
<link rel="stylesheet" href="/static/css/tokens.css">
<link rel="stylesheet" href="/static/css/shell.css">
</head>
<body>
<a class="rzp-skip" href="#main">Skip to content</a>
<header class="rzp-header" role="banner">
  <div class="rzp-header__inner">
    <a class="rzp-brand" href="/" aria-label="ReRoute Intelligence home">
      <img class="rzp-brand__mark" src="/static/img/header.svg" alt="Razorpay" width="118" height="25" loading="eager" decoding="async">
      <span class="rzp-brand__meta">
        <span class="rzp-brand__title">ReRoute Intelligence</span>
        <span class="rzp-brand__subtitle">Recovery operations &middot; Razorpay Test Mode</span>
      </span>
    </a>
    <div class="rzp-header__actions" aria-label="Environment">
      <span class="rzp-header__badge"><span class="rzp-header__badge-dot" aria-hidden="true"></span> Test Mode</span>
    </div>
  </div>
</header>
<nav class="rzp-nav" role="navigation" aria-label="Primary">
  <div class="rzp-nav__inner">
    <div class="rzp-nav__list" role="tablist" aria-label="Dashboard sections">
      <button role="tab" class="rzp-nav__item rzp-nav__item--active" aria-selected="true" tabindex="0" data-view="overview" id="tab-overview">Overview</button>
      <button role="tab" class="rzp-nav__item" aria-selected="false" tabindex="-1" data-view="queue" id="tab-queue">Recovery queue</button>
      <button role="tab" class="rzp-nav__item" aria-selected="false" tabindex="-1" data-view="detail" id="tab-detail">RecoveryCase detail</button>
      <button role="tab" class="rzp-nav__item" aria-selected="false" tabindex="-1" data-view="exceptions" id="tab-exceptions">PaymentExceptions</button>
      <button role="tab" class="rzp-nav__item" aria-selected="false" tabindex="-1" data-view="settings" id="tab-settings">Policy settings</button>
      <button role="tab" class="rzp-nav__item" aria-selected="false" tabindex="-1" data-view="investigation" id="tab-investigation">Investigation</button>
      <button role="tab" class="rzp-nav__item" aria-selected="false" tabindex="-1" data-view="evaluation" id="tab-evaluation">Evaluation</button>
    </div>
  </div>
</nav>
<main id="main" class="rzp-page" role="main" tabindex="-1">
  <div id="global-loading" class="rzp-loading-bar" aria-hidden="true" style="display:none" role="progressbar" aria-label="Loading dashboard"></div>
  <div id="global-error" class="rzp-card" role="alert" aria-live="assertive" style="display:none; border-left:4px solid var(--brand-color-error); margin-bottom: var(--brand-size-lg);"></div>

  <section id="overview" class="active" role="tabpanel" aria-labelledby="tab-overview">
    <p class="rzp-kicker">Executive</p>
    <h2 class="rzp-section-title">Overview</h2>
    <p class="rzp-section-lede">Revenue at risk, estimated recoverable, simulated and Test Mode outcomes — one ClaimTag per figure.</p>
    <div id="overview-content" class="grid rzp-grid">
      <div class="rzp-skeleton rzp-skeleton--card" aria-hidden="true"></div>
      <div class="rzp-skeleton rzp-skeleton--card" aria-hidden="true"></div>
      <div class="rzp-skeleton rzp-skeleton--card" aria-hidden="true"></div>
      <div class="rzp-skeleton rzp-skeleton--card" aria-hidden="true"></div>
    </div>
  </section>

  <section id="queue" role="tabpanel" aria-labelledby="tab-queue" hidden>
    <p class="rzp-kicker">Worklist</p>
    <h2 class="rzp-section-title">Recovery queue</h2>
    <p class="rzp-section-lede">Human review can submit only actions allowed by the policy. Actions use Test Mode or mock tools.</p>
    <div id="queue-content">
      <div class="rzp-skeleton" style="height: 180px; border-radius: var(--brand-border-radius-lg);" aria-hidden="true"></div>
    </div>
  </section>

  <section id="detail" role="tabpanel" aria-labelledby="tab-detail" hidden>
    <p class="rzp-kicker">Timeline</p>
    <h2 class="rzp-section-title">RecoveryCase detail</h2>
    <p class="rzp-section-lede">Raw event, decision, action, audit record, and outcome share one case timeline.</p>
    <div id="detail-content"><div class="rzp-skeleton" style="height: 120px; border-radius: var(--brand-border-radius-lg);" aria-hidden="true"></div></div>
    <h3 style="margin-top: var(--brand-size-xl);">Mock inbox</h3>
    <p class="rzp-muted" style="margin-bottom: var(--brand-size-sm);">Replies from mock contact and promise tools.</p>
    <div id="inbox-content"><div class="rzp-skeleton" style="height: 80px; border-radius: var(--brand-border-radius-lg);" aria-hidden="true"></div></div>
  </section>

  <section id="exceptions" role="tabpanel" aria-labelledby="tab-exceptions" hidden>
    <p class="rzp-kicker">Exceptions</p>
    <h2 class="rzp-section-title">PaymentExceptions</h2>
    <p class="rzp-section-lede">Open exceptions block customer-directed actions until evidence resolves them.</p>
    <div id="exceptions-content"><div class="rzp-skeleton" style="height: 100px; border-radius: var(--brand-border-radius-lg);" aria-hidden="true"></div></div>
  </section>

  <section id="settings" role="tabpanel" aria-labelledby="tab-settings" hidden>
    <p class="rzp-kicker">Controls</p>
    <h2 class="rzp-section-title">Policy settings</h2>
    <p class="rzp-section-lede">Quiet hours, contact limit, kill switch, and mock identity. Owner-only edits affect future actions.</p>
    <div id="settings-content"><div class="rzp-skeleton" style="height: 140px; border-radius: var(--brand-border-radius-lg);" aria-hidden="true"></div></div>
  </section>

  <section id="investigation" role="tabpanel" aria-labelledby="tab-investigation" hidden>
    <p class="rzp-kicker">Finding</p>
    <h2 class="rzp-section-title">Investigation</h2>
    <p class="rzp-section-lede">Top LeakFinding cohort, recoverable impact, confidence, and evidence.</p>
    <div id="investigation-content"><div class="rzp-skeleton" style="height: 160px; border-radius: var(--brand-border-radius-lg);" aria-hidden="true"></div></div>
  </section>

  <section id="evaluation" role="tabpanel" aria-labelledby="tab-evaluation" hidden>
    <p class="rzp-kicker">Reproducible</p>
    <h2 class="rzp-section-title">Evaluation</h2>
    <p class="rzp-section-lede">Published synthetic comparison — adaptive versus baseline across identical-case seeds.</p>
    <div id="evaluation-content"><div class="rzp-skeleton" style="height: 160px; border-radius: var(--brand-border-radius-lg);" aria-hidden="true"></div></div>
  </section>
</main>
<script>
const money = value => 'INR ' + (value / 100).toLocaleString('en-IN', {minimumFractionDigits: 2});
const html = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
const json = value => `<pre>${html(JSON.stringify(value, null, 2))}</pre>`;
const tag = (kind, text) => `<span class="tag rzp-badge rzp-badge--${html(kind)} ${html(kind)}">${html(text)}</span>`;
function emptyState(title, body) {
  return `<div class="rzp-empty" role="status"><p class="rzp-empty__title">${html(title)}</p><p class="rzp-empty__body">${html(body)}</p></div>`;
}
function render(data) {
  const top = data.executive.top_leak;
  const loading = document.getElementById('global-loading');
  if (loading) loading.style.display = 'none';
  document.querySelector('#overview-content').innerHTML = [
    `<article class="card rzp-card rzp-card--accent-estimated estimated"><div class="label rzp-card__label">Revenue at risk</div><div class="number rzp-card__number">${money(data.executive.revenue_at_risk)}</div><div style="margin-top:8px">${tag('estimated','ESTIMATED')}</div><p class="muted rzp-muted" style="margin-top:8px">Open RecoveryCases</p></article>`,
    `<article class="card rzp-card rzp-card--accent-estimated estimated"><div class="label rzp-card__label">Estimated recoverable value</div><div class="number rzp-card__number">${money(data.executive.estimated_value)}</div><div style="margin-top:8px">${tag('estimated','ESTIMATED')}</div></article>`,
    `<article class="card rzp-card rzp-card--accent-simulated simulated"><div class="label rzp-card__label">Adaptive simulated recovery</div><div class="number rzp-card__number">${money(data.evaluation.results.policies.adaptive.recovered_amount)}</div><div style="margin-top:8px">${tag('simulated','SIMULATED')}</div><p class="muted rzp-muted" style="margin-top:8px">30 seeds x 30 cases</p></article>`,
    `<article class="card rzp-card rzp-card--accent-test test-mode"><div class="label rzp-card__label">Test Mode recovered</div><div class="number rzp-card__number">${money(data.executive.test_mode_value)}</div><div style="margin-top:8px">${tag('test-mode','TEST MODE')}</div><p class="muted rzp-muted" style="margin-top:8px">Recorded Outcomes</p></article>`,
    `<article class="card rzp-card"><div class="label rzp-card__label">Open cases</div><div class="number rzp-card__number">${data.executive.open_cases}</div></article>`].join('');
  document.querySelector('#investigation-content').innerHTML = top ? `<article class="card rzp-card"><h3>${html(top.finding_id)}</h3><div style="margin:8px 0">${tag('estimated','ESTIMATED RECOVERABLE IMPACT')} <strong style="margin-left:6px">${money(top.recoverable_impact)}</strong></div><p class="rzp-muted">Confidence ${Math.round(top.confidence * 100)}%</p><h4 style="margin-top:16px">Cohort</h4>${json(top.cohort_filter)}<h4 style="margin-top:16px">Evidence</h4>${json(top.evidence)}</article>` : emptyState('No leak finding detected', 'Run finding detection from the API or ingest payment events to generate cohorts.');
  if (data.worklist.length === 0) {
    document.querySelector('#queue-content').innerHTML = emptyState('Queue is empty', 'No recovery cases match the current policy. Import CSV or webhook events to populate the queue.');
  } else {
    document.querySelector('#queue-content').innerHTML = `<div class="rzp-table-wrap"><table class="rzp-table"><thead><tr><th>Case</th><th>Evidence</th><th>Owner and budget</th><th>Policy</th><th>Human review</th></tr></thead><tbody>${data.worklist.map(c => `<tr><td>${html(c.case_id)}<br>${money(c.amount_at_risk)}<br><span class="muted rzp-muted">${html(c.state)}</span></td><td>${c.evidence ? `${html(c.evidence.event_type)}<br>${html(c.evidence.error_reason || c.evidence.status)}` : '<span class="rzp-muted">No payment event</span>'}${c.open_payment_exception ? '<br><span class="error rzp-error">Open PaymentException</span>' : ''}</td><td>${html(c.owner)}<br>${c.contact_budget} contacts left</td><td>${c.policy.allowed_actions.length ? 'Allowed: ' + c.policy.allowed_actions.map(html).join(', ') : '<span class="error rzp-error">Blocked</span>'}<br><span class="rzp-muted">${html(Object.values(c.blocked_reasons).flat().join(', '))}</span></td><td>${c.human_review.can_execute ? c.human_review.allowed_actions.map(a => `<button class="review rzp-btn rzp-btn--primary rzp-btn--sm" data-case="${html(c.case_id)}" data-action="${html(a)}">Approve ${html(a)}</button>`).join(' ') : '<span class="rzp-muted">No action permitted</span>'}</td></tr>`).join('')}</tbody></table></div>`;
  }
  document.querySelector('#detail-content').innerHTML = data.timeline.length ? data.timeline.map(t => `<article class="card rzp-card" style="margin-bottom: var(--brand-size-lg);"><h3>${html(t.case_id)}</h3>${t.events.map(e => `<div class="event ${e.kind === 'decision' ? 'event--decision' : e.kind === 'action' ? 'event--action' : e.kind === 'outcome' ? 'event--outcome' : ''}">${tag(e.kind === 'outcome' ? 'test-mode' : e.kind === 'decision' ? 'estimated' : 'simulated', e.kind.toUpperCase())} <span class="muted rzp-muted">${html(e.at || 'recorded decision')}</span>${json(e.data)}</div>`).join('') || '<p class="muted rzp-muted">No events.</p>'}</article>`).join('') : emptyState('No cases yet', 'Cases appear after ingestion. Each case shows raw events, decisions, actions, audit records, and outcomes on one timeline.');
  document.querySelector('#exceptions-content').innerHTML = data.payment_exceptions.length ? `<div style="display:grid; gap: var(--brand-size-lg);">${data.payment_exceptions.map(e => `<article class="card rzp-card"><h3>${html(e.kind)}</h3><p class="rzp-muted">${html(e.case_id)} &middot; ${html(e.state)}</p>${json(e.evidence)}</article>`).join('')}</div>` : emptyState('No PaymentExceptions', 'Open exceptions will appear here. Customer-directed actions are blocked while an exception is open.');
  document.querySelector('#settings-content').innerHTML = `<article class="card rzp-card"><p class="rzp-muted">Policy ${html(data.policy_settings.policy_version)}. Owner-only edits affect future Actions.</p>${json(data.policy_settings)}</article>`;
  document.querySelector('#evaluation-content').innerHTML = `<article class="card rzp-card simulated rzp-card--accent-simulated">${tag('simulated','SIMULATED')}<p class="rzp-muted" style="margin-top:8px">${data.evaluation.results.seeds.length} identical-case seeds, ${data.evaluation.results.cases_per_seed} cases per seed</p>${json(data.evaluation)}</article>`;
  document.querySelector('#inbox-content').innerHTML = data.mock_inbox.length ? `<div style="display:grid; gap: var(--brand-size-lg);">${data.mock_inbox.map(m => `<article class="card rzp-card"><h3>${html(m.tool)} for ${html(m.case_id)}</h3><div style="margin:8px 0">${tag('test-mode','MOCK')}</div><p class="rzp-muted">${html(m.status)} at ${html(m.executed_at || 'unknown time')}</p><code style="display:inline-block; margin-top:6px; padding:2px 6px; background: var(--brand-color-fill-quaternary); border-radius: var(--brand-border-radius-sm); font-family: var(--brand-font-family-code); font-size:12px;">${html(m.provider_reference || 'no provider reference')}</code><p style="margin-top:6px">${html(m.reply || 'Awaiting reply')}</p></article>`).join('')}</div>` : emptyState('No mock messages', 'Mock contact and promise replies appear here after actions are executed.');
}
function setActiveTab(view) {
  document.querySelectorAll('.rzp-nav__item').forEach(btn => {
    const isActive = btn.dataset.view === view;
    btn.classList.toggle('rzp-nav__item--active', isActive);
    btn.setAttribute('aria-selected', isActive ? 'true' : 'false');
    btn.tabIndex = isActive ? 0 : -1;
  });
  document.querySelectorAll('main section[role="tabpanel"]').forEach(s => {
    const isActive = s.id === view;
    s.classList.toggle('active', isActive);
    if (isActive) { s.removeAttribute('hidden'); s.focus?.(); } else { s.setAttribute('hidden', ''); }
  });
  // Keep legacy nav selector working for tests that query main section.active
  document.querySelectorAll('main section').forEach(s => s.classList.toggle('active', s.id === view));
  if (history.replaceState) history.replaceState(null, '', '#' + view);
}
document.querySelector('.rzp-nav').addEventListener('click', event => {
  const btn = event.target.closest('[data-view]');
  if (!btn) return;
  setActiveTab(btn.dataset.view);
});
document.querySelector('.rzp-nav').addEventListener('keydown', event => {
  const tabs = Array.from(document.querySelectorAll('.rzp-nav__item'));
  const current = tabs.findIndex(t => t.getAttribute('aria-selected') === 'true');
  if (event.key === 'ArrowRight') { event.preventDefault(); const next = tabs[(current + 1) % tabs.length]; next.focus(); setActiveTab(next.dataset.view); }
  if (event.key === 'ArrowLeft') { event.preventDefault(); const prev = tabs[(current - 1 + tabs.length) % tabs.length]; prev.focus(); setActiveTab(prev.dataset.view); }
  if (event.key === 'Home') { event.preventDefault(); tabs[0].focus(); setActiveTab(tabs[0].dataset.view); }
  if (event.key === 'End') { event.preventDefault(); tabs[tabs.length - 1].focus(); setActiveTab(tabs[tabs.length - 1].dataset.view); }
});
document.addEventListener('click', async event => {
  const btn = event.target.closest('.review');
  if (!btn) return;
  const original = btn.textContent;
  btn.disabled = true; btn.textContent = 'Approving…';
  try {
    const key = `review-${crypto.randomUUID()}`;
    const response = await fetch(`/api/v1/cases/${btn.dataset.case}/actions`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({action:btn.dataset.action, idempotency_key:key})});
    if (!response.ok) { alert(await response.text()); btn.disabled = false; btn.textContent = original; }
    else location.reload();
  } catch (err) {
    alert(String(err)); btn.disabled = false; btn.textContent = original;
  }
});
(function initTabs(){
  const hash = (location.hash || '').replace('#','');
  const valid = ['overview','queue','detail','exceptions','settings','investigation','evaluation'];
  if (valid.includes(hash)) setActiveTab(hash);
})();
const _loadingEl = document.getElementById('global-loading');
if (_loadingEl) _loadingEl.style.display = 'block';
fetch('/api/v1/dashboard').then(r => { if (!r.ok) throw new Error(r.status + ' ' + r.statusText); return r.json(); }).then(render).catch(error => {
  const loading = document.getElementById('global-loading');
  if (loading) loading.style.display = 'none';
  const box = document.getElementById('global-error');
  if (box) { box.style.display = 'block'; box.innerHTML = `<strong style="color: var(--brand-color-error);">Could not load dashboard</strong><p class="rzp-muted" style="margin-top:6px">${html(error)}</p><p class="rzp-muted" style="margin-top:6px">Check the API is running and refresh. Data remains available at <code>/api/v1/dashboard</code>.</p>`; }
  else { document.querySelector('main').innerHTML = `<p class="error rzp-error">Could not load dashboard: ${html(error)}</p>`; }
});
</script>
</body>
</html>"""
