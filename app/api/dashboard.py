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
DASHBOARD_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ReRoute | Payment recovery operations</title><link rel="icon" href="/static/logos/razorpay.svg"><style>
@font-face{font-family:Inter;src:url('/static/fonts/inter-400.woff2')}@font-face{font-family:Inter;src:url('/static/fonts/inter-600.woff2');font-weight:600}@font-face{font-family:Inter;src:url('/static/fonts/inter-700.woff2');font-weight:700}
:root{--white:#fff;--cloud:#f8fafc;--navy:#192839;--slate:#768ea7;--grey:#dfe3e9;--blue:#305eff;--deep:#0052b4;--success:#16844b;--danger:#c63838;--radius:8px;--space:8px;--shadow:0 1px 2px rgba(25,40,57,.06)}*{box-sizing:border-box}body{margin:0;color:var(--navy);background:var(--white);font:14px/1.57 Inter,system-ui,sans-serif}button,input,select{font:inherit}button{min-height:44px;border:1px solid var(--grey);border-radius:var(--radius);background:var(--white);color:var(--navy);padding:0 16px;cursor:pointer;transition:.2s ease}button:hover{border-color:var(--blue);color:var(--blue)}button:focus-visible{outline:3px solid #abc6ff;outline-offset:2px}button:disabled{cursor:not-allowed;opacity:.55}.primary{background:var(--blue);border-color:var(--blue);color:#fff}.primary:hover{background:var(--deep);border-color:var(--deep);color:#fff}.shell{min-height:100vh}.topbar{height:72px;border-bottom:1px solid var(--grey);display:flex;align-items:center;justify-content:space-between;padding:0 clamp(16px,4vw,64px);gap:24px}.brand{display:flex;align-items:center;gap:16px;min-width:220px}.brand img{width:116px;height:auto}.brand-divider{height:24px;border-left:1px solid var(--grey)}.brand-product{font-size:13px;color:var(--slate);white-space:nowrap}.user{display:flex;align-items:center;gap:12px;color:var(--slate)}.avatar{display:grid;place-items:center;width:32px;height:32px;border-radius:50%;background:#f0f6ff;color:var(--deep);font-weight:600}.layout{display:grid;grid-template-columns:224px minmax(0,1fr);min-height:calc(100vh - 72px)}aside{border-right:1px solid var(--grey);padding:24px 16px;background:var(--cloud)}.nav-label,.eyebrow{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--slate);font-weight:600}.nav-label{padding:0 12px 8px}.nav{display:grid;gap:4px}.nav button{border:0;text-align:left;padding:0 12px;min-height:40px;background:transparent;color:var(--slate)}.nav button:hover{background:#eef3f8;color:var(--navy)}.nav button.active{background:#e6efff;color:var(--deep);font-weight:600}.aside-footer{margin-top:32px;padding:16px 12px;border-top:1px solid var(--grey);color:var(--slate);font-size:12px}.main{min-width:0;padding:clamp(24px,4vw,56px) clamp(16px,4vw,64px);max-width:1480px;width:100%}.view{display:none}.view.active{display:block}.page-head{display:flex;justify-content:space-between;align-items:flex-start;gap:24px;margin-bottom:32px}.page-head h1{font:600 clamp(28px,3vw,42px)/1.1 Inter,system-ui,sans-serif;letter-spacing:-.04em;margin:4px 0 8px}.page-head p{margin:0;color:var(--slate);max-width:62ch}.headline{display:flex;align-items:center;gap:8px}.status{display:inline-flex;align-items:center;min-height:24px;padding:0 8px;border-radius:4px;background:#f0f6ff;color:var(--deep);font-size:12px;font-weight:600}.status.success{background:#f6ffed;color:var(--success)}.status.warn{background:#fffbe6;color:#9b6900}.status.error{background:#fff2f0;color:var(--danger)}.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));border-top:1px solid var(--grey);border-bottom:1px solid var(--grey);margin-bottom:40px}.metric{padding:20px 24px 22px 0;border-right:1px solid var(--grey);margin:16px 24px 16px 0}.metric:last-child{border:0}.metric .value{font-size:26px;font-weight:600;letter-spacing:-.03em;margin-top:4px}.metric p{margin:4px 0 0;color:var(--slate);font-size:12px}.section-head{display:flex;justify-content:space-between;align-items:end;gap:16px;margin:0 0 16px}.section-head h2{font-size:20px;margin:0;letter-spacing:-.02em}.section-head p{margin:0;color:var(--slate);font-size:13px}.panel{border:1px solid var(--grey);border-radius:var(--radius);background:var(--white);box-shadow:var(--shadow);overflow:hidden}.panel-head{padding:20px 24px;border-bottom:1px solid var(--grey);display:flex;align-items:center;justify-content:space-between;gap:16px}.panel-head h3{margin:0;font-size:16px}.panel-body{padding:24px}.story{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(280px,.75fr);gap:24px;margin-bottom:40px}.risk{background:var(--navy);color:white;border-radius:var(--radius);padding:32px;min-height:250px;display:flex;flex-direction:column;justify-content:space-between}.risk .eyebrow{color:#abc6ff}.risk h2{font-size:30px;line-height:1.1;letter-spacing:-.04em;margin:10px 0}.risk .amount{font-size:40px;font-weight:600;letter-spacing:-.05em}.risk p{color:#c9d4df;margin:6px 0 0}.trace-summary{padding:24px}.trace-summary h3{margin:0 0 16px;font-size:18px}.trace-step{display:flex;gap:12px;align-items:flex-start;padding:12px 0;border-bottom:1px solid var(--grey)}.trace-step:last-child{border:0}.step-dot{width:24px;height:24px;border-radius:50%;display:grid;place-items:center;background:#e6efff;color:var(--deep);font-size:12px;font-weight:600;flex:0 0 auto}.step-dot.fail{background:#fff2f0;color:var(--danger)}.step-dot.done{background:#f6ffed;color:var(--success)}.trace-step strong{display:block}.trace-step small{color:var(--slate)}.lower-grid{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(280px,.7fr);gap:24px}.case-list{display:grid}.case-row{display:grid;grid-template-columns:minmax(170px,1.2fr) minmax(110px,.6fr) minmax(150px,1fr) auto;gap:16px;align-items:center;padding:16px 24px;border-bottom:1px solid var(--grey)}.case-row:last-child{border-bottom:0}.case-row:hover{background:var(--cloud)}.case-id{font-family:monospace;color:var(--deep);font-size:12px}.case-title{font-weight:600}.case-sub{font-size:12px;color:var(--slate)}.mono{font-family:monospace;font-size:12px}.empty,.loading{padding:40px 24px;text-align:center;color:var(--slate)}.error-box{padding:16px;background:#fff2f0;border:1px solid #ffccc7;border-radius:var(--radius);color:var(--danger)}table{width:100%;border-collapse:collapse}th,td{padding:16px 20px;border-bottom:1px solid var(--grey);text-align:left;vertical-align:top}th{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--slate);background:var(--cloud)}td{font-size:13px}tr:last-child td{border-bottom:0}.detail-grid{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(280px,.7fr);gap:24px}.event{display:grid;grid-template-columns:112px minmax(0,1fr);gap:16px;padding:20px 24px;border-bottom:1px solid var(--grey)}.event:last-child{border-bottom:0}.event-time{font-family:monospace;font-size:11px;color:var(--slate);overflow-wrap:anywhere}.event h4{margin:0 0 6px;font-size:14px}.event pre{margin:8px 0 0;background:var(--cloud);padding:12px;white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.5 monospace;border-radius:4px}.form-row{display:grid;grid-template-columns:1fr 1fr;gap:16px}.field{display:grid;gap:6px;margin-bottom:16px}.field label{font-size:12px;color:var(--slate)}.field input,.field select{height:44px;border:1px solid var(--grey);border-radius:var(--radius);padding:0 12px;color:var(--navy);background:#fff}.field input:focus,.field select:focus{outline:3px solid #abc6ff;border-color:var(--blue)}.json{font:12px/1.5 monospace;background:var(--cloud);padding:16px;overflow:auto;white-space:pre-wrap;margin:0}.stack{display:grid;gap:16px}.pill-row{display:flex;flex-wrap:wrap;gap:8px}.action-row{display:flex;gap:8px;flex-wrap:wrap}.notice{padding:16px;background:#f0f6ff;border:1px solid #abc6ff;border-radius:var(--radius);color:var(--deep)}@media(max-width:900px){.layout{grid-template-columns:1fr}aside{border-right:0;border-bottom:1px solid var(--grey);padding:12px 16px;position:sticky;top:0;z-index:2}.nav{display:flex;overflow:auto}.nav-label,.aside-footer{display:none}.nav button{white-space:nowrap}.metrics{grid-template-columns:repeat(2,1fr)}.story,.lower-grid,.detail-grid{grid-template-columns:1fr}.case-row{grid-template-columns:1fr auto}.case-row>:nth-child(2),.case-row>:nth-child(3){display:none}}@media(max-width:520px){.topbar{height:64px;padding:0 16px}.brand{min-width:0}.brand-product,.brand-divider,.user span{display:none}.main{padding:24px 16px}.page-head{display:block}.page-head .action-row{margin-top:20px}.metrics{margin-left:-16px;margin-right:-16px;padding:0 16px}.metric{padding-right:12px;margin-right:12px}.metric .value{font-size:22px}.risk{padding:24px}.risk .amount{font-size:34px}.panel-head,.panel-body{padding:16px}th,td{padding:12px 10px}.event{grid-template-columns:1fr;padding:16px}.form-row{grid-template-columns:1fr}}
</style></head><body><div class="shell"><header class="topbar"><div class="brand"><img src="/static/logos/razorpay.svg" alt="Razorpay"><span class="brand-divider"></span><span class="brand-product">ReRoute recovery</span></div><div class="user"><span>Razorpay Test Mode</span><span class="avatar" aria-label="Operations user">OP</span></div></header><div class="layout"><aside><div class="nav-label">Workspace</div><nav class="nav" aria-label="Dashboard sections"><button class="active" data-view="overview">Overview</button><button data-view="queue">Recovery queue</button><button data-view="detail">RecoveryCase detail</button><button data-view="exceptions">PaymentExceptions</button><button data-view="settings">Policy settings</button><button data-view="investigation">Investigation</button><button data-view="evaluation">Evaluation</button></nav><div class="aside-footer">Recovery operations<br><span class="mono">policy-backed actions</span></div></aside><main class="main"><div id="app"><div class="loading">Loading recovery operations...</div></div></main></div></div><script>
const money=v=>'INR '+((v||0)/100).toLocaleString('en-IN',{minimumFractionDigits:2});const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const pretty=v=>esc(JSON.stringify(v,null,2));const badge=(text,kind='')=>`<span class="status ${kind}">${esc(text)}</span>`;let state={data:null,view:'overview'};
function eventTitle(e){return {'raw event':'Provider event','decision':'Policy decision','action':'Recovery action','audit':'Audit record','outcome':'Provider outcome'}[e.kind]||e.kind}
function renderOverview(d){const c=d.worklist[0], events=d.timeline.find(t=>t.case_id===c?.case_id)?.events||[];return `<div class="page-head"><div><div class="eyebrow">Payment recovery operations</div><div class="headline"><h1>Recover revenue with evidence</h1>${badge('Test Mode')}</div><p>Review what happened, why ReRoute chose an action, and what the provider recorded next.</p></div><div class="action-row"><button class="primary" data-view="queue">Open recovery queue</button></div></div><div class="metrics"><div class="metric"><div class="eyebrow">Revenue at risk</div><div class="value">${money(d.executive.revenue_at_risk)}</div><p>Open recovery cases</p></div><div class="metric"><div class="eyebrow">Estimated recoverable</div><div class="value">${money(d.executive.estimated_value)}</div><p>Top persisted finding</p></div><div class="metric"><div class="eyebrow">Test Mode recovered</div><div class="value">${money(d.executive.test_mode_value)}</div><p>Recorded outcomes</p></div><div class="metric"><div class="eyebrow">Open cases</div><div class="value">${d.executive.open_cases}</div><p>Needs review or action</p></div></div>${c?`<div class="section-head"><div><h2>Case in focus</h2><p>The clearest recorded path through a failed payment.</p></div><button data-view="detail">View full case</button></div><div class="story"><article class="risk"><div><div class="eyebrow">Money at risk</div><h2>${esc(c.case_id)}</h2><div class="amount">${money(c.amount_at_risk)}</div><p>${esc(c.evidence?.error_reason||c.evidence?.status||'No provider event recorded')}</p></div><div>${badge(esc(c.state))}<span class="status">${esc(c.evidence?.method||'payment')}</span></div></article><article class="panel trace-summary"><h3>Recorded execution trace</h3>${events.length?events.slice(-4).map((e,i)=>`<div class="trace-step"><span class="step-dot ${e.kind==='outcome'?'done':e.kind==='action'?'fail':''}">${i+1}</span><div><strong>${eventTitle(e)}</strong><small>${esc(e.at||'Recorded without timestamp')}</small></div></div>`).join(''):'<div class="empty">No trace events recorded.</div>'}</article></div>`:'<div class="panel empty">No recovery cases have been recorded.</div>'}<div class="lower-grid"><div class="panel"><div class="panel-head"><h3>Recovery queue</h3><button data-view="queue">View all</button></div>${renderCaseRows(d.worklist.slice(0,4))}</div><div class="panel"><div class="panel-head"><h3>Policy signal</h3></div><div class="panel-body">${d.investigation?`<div class="eyebrow">Top finding</div><h3>${esc(d.investigation.finding_id)}</h3><p>${money(d.investigation.recoverable_impact)} estimated recoverable impact at ${Math.round(d.investigation.confidence*100)}% confidence.</p>`:'<div class="empty">No leak finding has been detected.</div>'}</div></div></div>`}
function renderCaseRows(cases){return cases.length?`<div class="case-list">${cases.map(c=>`<div class="case-row"><div><div class="case-title">${esc(c.case_id)}</div><div class="case-sub">${esc(c.evidence?.error_reason||c.evidence?.status||'No payment evidence')}</div></div><div><div class="case-sub">At risk</div><strong>${money(c.amount_at_risk)}</strong></div><div>${badge(c.state,c.state==='recovered'?'success':c.state==='escalated'?'warn':'')}</div><button data-view="detail" data-case="${esc(c.case_id)}">Review</button></div>`).join('')}</div>`:'<div class="empty">No recovery cases match this view.</div>'}
function renderQueue(d){return `<div class="page-head"><div><div class="eyebrow">Operations</div><h1>Recovery queue</h1><p>Policy-backed work ordered for human review. Each action stays within the recorded policy decision.</p></div></div><div class="panel"><div class="panel-head"><h3>${d.worklist.length} cases</h3><span class="case-sub">Sorted by escalation, exception, and expected value</span></div><div style="overflow:auto"><table><thead><tr><th>Case</th><th>Evidence</th><th>Owner</th><th>Policy output</th><th>Review</th></tr></thead><tbody>${d.worklist.length?d.worklist.map(c=>`<tr><td><strong>${esc(c.case_id)}</strong><br><span class="case-sub">${money(c.amount_at_risk)} at risk</span></td><td>${c.evidence?`${esc(c.evidence.event_type)}<br><span class="case-sub">${esc(c.evidence.error_reason||c.evidence.status)}</span>`:'No payment event'}${c.open_payment_exception?`<br>${badge('Open exception','error')}`:''}</td><td>${esc(c.owner)}<br><span class="case-sub">${c.contact_budget} contacts left</span></td><td>${c.policy.allowed_actions.length?`<div class="pill-row">${c.policy.allowed_actions.map(a=>badge(a)).join('')}</div>`:`${badge('Blocked','error')}<br><span class="case-sub">${esc(Object.values(c.blocked_reasons).flat().join(', '))}</span>`}</td><td>${c.human_review.can_execute?c.human_review.allowed_actions.map(a=>`<button class="primary review" data-case="${esc(c.case_id)}" data-action="${esc(a)}">Approve ${esc(a)}</button>`).join(' '):'<span class="case-sub">No action permitted</span>'}</td></tr>`).join(''):'<tr><td colspan="5" class="empty">No recovery cases have been recorded.</td></tr>'}</tbody></table></div></div>`}
function renderDetail(d){const t=d.timeline[0];return `<div class="page-head"><div><div class="eyebrow">Evidence trail</div><h1>Case detail</h1><p>One case, in the order the system recorded it. Provider events and operator decisions are kept separate.</p></div></div>${t?`<div class="detail-grid"><div class="panel"><div class="panel-head"><h3>${esc(t.case_id)}</h3>${badge('Recorded trace')}</div>${t.events.length?t.events.map(e=>`<div class="event"><div class="event-time">${esc(e.at||'No timestamp')}</div><div><h4>${eventTitle(e)} ${badge(e.kind,e.kind==='outcome'?'success':e.kind==='action'?'error':'')}</h4><pre>${pretty(e.data)}</pre></div></div>`).join(''):'<div class="empty">No events recorded for this case.</div>'}</div><div class="stack"><div class="panel"><div class="panel-head"><h3>Mock inbox</h3></div>${d.mock_inbox.length?d.mock_inbox.map(m=>`<div class="panel-body"><strong>${esc(m.tool)} for ${esc(m.case_id)}</strong><p>${esc(m.reply||'Awaiting reply')}</p><span class="mono">${esc(m.provider_reference||'No provider reference')}</span></div>`).join(''):'<div class="empty">No mock messages have been sent.</div>'}</div></div></div>`:'<div class="panel empty">No case trace is available.</div>'}`}
function renderSimple(d,view){const titles={exceptions:['Exceptions','Provider and payment records that need attention'],settings:['Policy settings','The active rules that govern future recovery actions'],investigation:['Investigation','Persisted findings explain where revenue is leaking'],evaluation:['Evaluation','Simulation results are labeled separately from Test Mode outcomes']};const [title,desc]=titles[view];let body='';if(view==='exceptions')body=d.payment_exceptions.length?d.payment_exceptions.map(e=>`<div class="panel-body"><div class="headline"><h3>${esc(e.kind)}</h3>${badge(e.state,e.state==='open'?'error':'success')}</div><p>${esc(e.case_id)}</p><pre class="json">${pretty(e.evidence)}</pre></div>`).join(''):'<div class="empty">No PaymentExceptions have been recorded.</div>';if(view==='settings')body=`<div class="panel-body"><div class="notice">Policy ${esc(d.policy_settings.policy_version)} applies to future Actions. Owner-only edits are kept separate from case evidence.</div><pre class="json">${pretty(d.policy_settings)}</pre></div>`;if(view==='investigation')body=d.investigation?`<div class="panel-body"><div class="eyebrow">Finding</div><h3>${esc(d.investigation.finding_id)}</h3><p>${money(d.investigation.recoverable_impact)} estimated recoverable impact. Confidence ${Math.round(d.investigation.confidence*100)}%.</p><h4>Cohort</h4><pre class="json">${pretty(d.investigation.cohort_filter)}</pre><h4>Evidence</h4><pre class="json">${pretty(d.investigation.evidence)}</pre></div>`:'<div class="empty">No leak finding has been detected.</div>';if(view==='evaluation')body=`<div class="panel-body"><div class="headline">${badge('SIMULATED')}<strong>${d.evaluation.results.seeds.length} seeds, ${d.evaluation.results.cases_per_seed} cases per seed</strong></div><pre class="json">${pretty(d.evaluation)}</pre></div>`;return `<div class="page-head"><div><div class="eyebrow">ReRoute</div><h1>${title}</h1><p>${desc}</p></div></div><div class="panel">${body}</div>`}
function render(){const d=state.data;const content=state.view==='overview'?renderOverview(d):state.view==='queue'?renderQueue(d):state.view==='detail'?renderDetail(d):renderSimple(d,state.view);document.querySelector('#app').innerHTML=content;document.querySelectorAll('[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===state.view));}
</script></body></html>"""
