from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.api.evaluations import get_published_evaluation
from app.db.tables import (
    ActionEvent,
    AuditEvent,
    Decision,
    LeakFinding,
    Outcome,
    PaymentEvent,
    RecoveryCase,
)
from app.leak_analysis import finding_sort_key
from app.policy import evaluate_policy

router = APIRouter(tags=["dashboard"])


def _worklist_sort_key(item: dict) -> tuple[int, int, str]:
    # ADR 0006 order: escalations -> aged PaymentExceptions -> eligible by expected net value -> investigated by age
    # PaymentExceptions not yet introduced in #30, so eligible is second after escalated
    state = item["state"]
    if state == "escalated":
        rank = 0
        # aged: older opened_at first
        secondary = item.get("opened_at") or ""
        return (rank, 0, secondary)
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
        findings = session.scalars(select(LeakFinding)).all()
        findings.sort(key=finding_sort_key)
        cases = session.scalars(select(RecoveryCase).order_by(RecoveryCase.case_id)).all()
        worklist = [_case_summary(session, case, request) for case in cases]
        # Worklist ordering per ADR 0006: escalations -> aged PaymentExceptions -> eligible by expected net value -> investigated by age
        # PaymentExceptions not yet implemented in #30, so sort as escalated, then eligible, then investigated, then others
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


def _case_summary(session, case: RecoveryCase, request: Request) -> dict:
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
        request.app.state.quiet_hours_start,
        request.app.state.quiet_hours_end,
    )
    # expected net value for worklist sorting: prefer persisted Decision, else rank eligible via RecoveryModel
    if decision is not None:
        expected_value = decision.expected_value
    elif case.state == "eligible":
        from app.db.tables import Customer

        customer = session.get(Customer, case.customer_id) if case.customer_id else None
        ranked = request.app.state.recovery_model.rank(case, customer, policy.allowed_actions)
        expected_value = int(ranked[0]["expected_net_value"]) if ranked else None
    else:
        expected_value = None
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
            "at": None,
            "data": {
                "selected_action": decision.selected_action,
                "expected_value": decision.expected_value,
                "policy_version": decision.policy_version,
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
        "raw_body": (
            payment.raw_body.decode("utf-8", errors="replace") if payment.raw_body else None
        ),
    }


def _action(action: ActionEvent) -> dict:
    return {
        "case_id": action.case_id,
        "tool": action.tool,
        "status": action.status,
        "provider_reference": action.provider_reference,
        "executed_at": _time(action.executed_at),
    }


def _time(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


# ruff: noqa: E501
DASHBOARD_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ReRoute Intelligence</title><style>
:root { color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui; background:#10151c; color:#e8edf2; }
body { margin:0; } header { padding:26px max(5vw,24px); background:#17212b; border-bottom:1px solid #33414e; }
h1 { margin:0; font-size:1.4rem; } header p { margin:6px 0 0; color:#aebdca; }
nav { display:flex; gap:8px; overflow:auto; padding:14px max(5vw,24px); position:sticky; top:0; background:#10151c; border-bottom:1px solid #33414e; }
button { color:inherit; background:#243444; border:1px solid #426179; border-radius:6px; padding:8px 10px; cursor:pointer; } button:hover { background:#31516b; }
main { max-width:1180px; margin:auto; padding:24px; } section { display:none; } section.active { display:block; }
.grid { display:grid; gap:16px; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); } .card { background:#17212b; border:1px solid #33414e; border-radius:9px; padding:16px; }
.label { color:#aebdca; font-size:.8rem; text-transform:uppercase; letter-spacing:.08em; } .number { font-size:1.7rem; margin-top:5px; }
.estimated { border-left:4px solid #e0a847; } .simulated { border-left:4px solid #7eb2e0; } .test-mode { border-left:4px solid #61c795; }
.tag { display:inline-block; padding:3px 6px; border-radius:4px; font-size:.75rem; margin-right:6px; background:#263b4b; } .tag.estimated { color:#f1c46c; } .tag.simulated { color:#a5d1f6; } .tag.test-mode { color:#92e5b7; }
table { border-collapse:collapse; width:100%; margin-top:12px; } th,td { border-bottom:1px solid #33414e; padding:10px 6px; text-align:left; vertical-align:top; } th { color:#aebdca; font-size:.8rem; }
pre { white-space:pre-wrap; overflow-wrap:anywhere; background:#0c1117; padding:12px; border-radius:6px; } .event { border-left:2px solid #426179; padding:0 0 18px 14px; margin-left:8px; } .muted { color:#aebdca; } .error { color:#ffaaa4; }
</style></head><body><header><h1>ReRoute Intelligence</h1><p>Recovery operations in Razorpay Test Mode</p></header>
<nav><button data-view="executive">Executive</button><button data-view="investigation">Investigation</button><button data-view="worklist">Worklist</button><button data-view="timeline">Timeline</button><button data-view="evaluation">Evaluation</button><button data-view="inbox">Mock inbox</button></nav>
<main><section id="executive" class="active"><h2>Executive</h2><div id="executive-content" class="grid"></div></section><section id="investigation"><h2>Investigation</h2><div id="investigation-content"></div></section><section id="worklist"><h2>Worklist</h2><p class="muted">Human review can submit only actions allowed by the policy. Actions use Test Mode or mock tools.</p><div id="worklist-content"></div></section><section id="timeline"><h2>Timeline</h2><p class="muted">Raw event, decision, action, audit record, and outcome share one case timeline.</p><div id="timeline-content"></div></section><section id="evaluation"><h2>Evaluation</h2><div id="evaluation-content"></div></section><section id="inbox"><h2>Mock inbox</h2><div id="inbox-content"></div></section></main>
<script>
const money = value => 'INR ' + (value / 100).toLocaleString('en-IN', {minimumFractionDigits: 2});
const html = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
const json = value => `<pre>${html(JSON.stringify(value, null, 2))}</pre>`;
const tag = (kind, text) => `<span class="tag ${kind}">${text}</span>`;
function render(data) {
  const top = data.executive.top_leak;
  document.querySelector('#executive-content').innerHTML = [
    `<article class="card estimated"><div class="label">Estimated recoverable value</div><div class="number">${money(data.executive.estimated_value)}</div>${tag('estimated','ESTIMATED')}</article>`,
    `<article class="card simulated"><div class="label">Adaptive simulated recovery</div><div class="number">${money(data.evaluation.results.policies.adaptive.recovered_amount)}</div>${tag('simulated','SIMULATED')}</article>`,
    `<article class="card test-mode"><div class="label">Test Mode recovered</div><div class="number">${money(data.executive.test_mode_value)}</div>${tag('test-mode','TEST MODE')}</article>`,
    `<article class="card"><div class="label">Open cases</div><div class="number">${data.executive.open_cases}</div></article>`].join('');
  document.querySelector('#investigation-content').innerHTML = top ? `<article class="card"><h3>${html(top.finding_id)}</h3>${tag('estimated','ESTIMATED RECOVERABLE IMPACT')}<strong>${money(top.recoverable_impact)}</strong><p>Confidence ${Math.round(top.confidence * 100)}%</p><h4>Cohort</h4>${json(top.cohort_filter)}<h4>Evidence</h4>${json(top.evidence)}</article>` : '<p class="muted">No leak finding has been detected.</p>';
  document.querySelector('#worklist-content').innerHTML = `<table><thead><tr><th>Case</th><th>Evidence</th><th>Selected action</th><th>Policy</th><th>Human review</th></tr></thead><tbody>${data.worklist.map(c => `<tr><td>${html(c.case_id)}<br>${money(c.amount_at_risk)}<br><span class="muted">${html(c.state)}</span></td><td>${c.evidence ? `${html(c.evidence.event_type)}<br>${html(c.evidence.error_reason || c.evidence.status)}` : 'No payment event'}</td><td>${html(c.selected_action || 'None')} ${c.expected_value !== null ? `<br>${tag('estimated','EST.')} ${money(c.expected_value)}` : ''}</td><td>${c.policy.allowed_actions.length ? 'Allowed: ' + c.policy.allowed_actions.map(html).join(', ') : '<span class="error">Blocked</span>'}</td><td>${c.human_review.can_execute ? c.human_review.allowed_actions.map(a => `<button class="review" data-case="${html(c.case_id)}" data-action="${html(a)}">Approve ${html(a)}</button>`).join(' ') : 'No action permitted'}</td></tr>`).join('')}</tbody></table>`;
  document.querySelector('#timeline-content').innerHTML = data.timeline.map(t => `<article class="card"><h3>${html(t.case_id)}</h3>${t.events.map(e => `<div class="event">${tag(e.kind === 'outcome' ? 'test-mode' : e.kind === 'decision' ? 'estimated' : 'simulated', e.kind.toUpperCase())}<span class="muted">${html(e.at || 'recorded decision')}</span>${json(e.data)}</div>`).join('') || '<p class="muted">No events.</p>'}</article>`).join('');
  document.querySelector('#evaluation-content').innerHTML = `<article class="card simulated">${tag('simulated','SIMULATED')}<p>${data.evaluation.results.seeds.length} identical-case seeds, ${data.evaluation.results.cases_per_seed} cases per seed</p>${json(data.evaluation)}</article>`;
  document.querySelector('#inbox-content').innerHTML = data.mock_inbox.length ? data.mock_inbox.map(m => `<article class="card"><h3>${html(m.tool)} for ${html(m.case_id)}</h3>${tag('test-mode','MOCK')}<p>${html(m.status)} at ${html(m.executed_at || 'unknown time')}</p><code>${html(m.provider_reference || 'no provider reference')}</code></article>`).join('') : '<p class="muted">No mock messages have been sent.</p>';
}
document.querySelector('nav').addEventListener('click', event => { const view = event.target.dataset.view; if (!view) return; document.querySelectorAll('main section').forEach(s => s.classList.toggle('active', s.id === view)); });
document.addEventListener('click', async event => { if (!event.target.matches('.review')) return; const key = `review-${crypto.randomUUID()}`; const response = await fetch(`/api/v1/cases/${event.target.dataset.case}/actions`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({action:event.target.dataset.action, idempotency_key:key})}); if (!response.ok) alert(await response.text()); else location.reload(); });
fetch('/api/v1/dashboard').then(r => r.json()).then(render).catch(error => { document.querySelector('main').innerHTML = `<p class="error">Could not load dashboard: ${html(error)}</p>`; });
</script></body></html>"""
