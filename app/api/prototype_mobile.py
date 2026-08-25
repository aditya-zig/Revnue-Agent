from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["prototype"])

MOBILE_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>ReRoute — Merchant recovery (in-app prototype)</title><style>
:root{color-scheme:dark;font-family:Inter,ui-sans-serif,system-ui;background:#0e141c;color:#e8edf2}
*{box-sizing:border-box}body{margin:0}
.proto-note{background:#7a5a1f;color:#fff;text-align:center;padding:6px 10px;font-size:.8rem}
#app{padding-bottom:84px}
#switcher{position:fixed;left:50%;bottom:18px;transform:translateX(-50%);display:flex;gap:10px;align-items:center;background:#0c1117;border:1px solid #4a647a;padding:8px 12px;border-radius:999px;box-shadow:0 8px 24px rgba(0,0,0,.5);z-index:9999}
#switcher button{background:#1e3245;border:1px solid #426179;color:#e8edf2;border-radius:999px;padding:6px 10px}
.tag{display:inline-block;padding:2px 6px;border-radius:4px;font-size:.7rem;border:1px solid #33414e;background:#1e3245}
.tag.est{color:#f1c46c;border-color:#7a5a1f}.tag.sim{color:#a5d1f6;border-color:#2e5a85}.tag.test{color:#92e5b7;border-color:#2a6b4a}.tag.block{color:#ffaaa4;border-color:#7a3a38}
.card{background:#17212b;border:1px solid #33414e;border-radius:10px;padding:12px;margin:10px}
.pill{display:inline-block;padding:2px 7px;border-radius:999px;font-size:.7rem}
.pill.ok{background:#1a3a2a;color:#92e5b7;border:1px solid #2a6b4a}.pill.wait{background:#2e2a1a;color:#f1c46c;border:1px solid #7a5a1f}.pill.ex{background:#3a1a1a;color:#ffaaa4;border:1px solid #7a3a38}.pill.esc{background:#2a1a3a;color:#d8b4fe;border:1px solid #5a347a}
.bottom-nav{position:fixed;bottom:0;left:0;right:0;display:flex;justify-content:space-around;background:#111a23;border-top:1px solid #33414e;padding:6px 0;z-index:5}
.bottom-nav a{text-decoration:none;color:#cbd5e1;font-size:.7rem;text-align:center;flex:1}
.bottom-nav a.active{color:#fff;font-weight:700}
.grid{display:grid;gap:10px;grid-template-columns:1fr 1fr}
@media(min-width:700px){.grid{grid-template-columns:repeat(4,1fr)}}
table{width:100%;border-collapse:collapse}th,td{border-bottom:1px solid #33414e;padding:8px 6px;text-align:left;font-size:.85rem}th{color:#aebdca;font-size:.7rem;text-transform:uppercase}
</style></head><body>
<header class="proto-note"><strong>IN-APP PROTOTYPE</strong> — served at <code>/prototype/mobile?variant=A/B/C</code>. Throwaway, not production. Variants switch via URL + floating pill. Android APK wraps this route in a WebView.</header>
<div id="app"></div>
<div id="switcher"><button id="prev">←</button><div id="label" style="min-width:210px;text-align:center;font-size:.85rem"></div><button id="next">→</button></div>
<nav class="bottom-nav" id="bnav"></nav>
<script>
const m={overview:{risk:8472300,est:3124000,rec:420000,sim:2985000,open:18},queue:[
{ id:"RC-0831", amt:199900, st:"escalated", cust:"CUST-441", err:"help requested", age:"2d", exp:41200, block:false},
{ id:"RC-0824", amt:49900, st:"exception", cust:"CUST-109", err:"debit reported", age:"4h", exp:0, block:true},
{ id:"RC-0829", amt:99900, st:"eligible", cust:"CUST-882", err:"insufficient_funds", age:"5h", exp:78500, block:false},
{ id:"RC-0830", amt:149900, st:"eligible", cust:"CUST-903", err:"gateway_timeout", age:"1h", exp:92300, block:false},
{ id:"RC-0827", amt:49900, st:"awaiting_outcome", cust:"CUST-771", err:"gateway_timeout", age:"18h", exp:0, block:false},
]}; const money=n=>'INR '+(n/100).toLocaleString('en-IN',{minimumFractionDigits:2});
function pill(s){if(s==='eligible')return'<span class=pill ok>eligible</span>';if(s==='exception')return'<span class=pill ex>PaymentException — blocked</span>';if(s==='awaiting_outcome')return'<span class=pill wait>awaiting outcome</span>';if(s==='escalated')return'<span class=pill esc>escalated to owner</span>';return '<span class=pill>'+s+'</span>'}
function getV(){const p=new URLSearchParams(location.search).get('variant');return p==='B'||p==='C'?'B'!==p?p:'B':p==='C'?'C':'A'}
function setV(k){const u=new URL(location.href);u.searchParams.set('variant',k);history.replaceState(null,'',u);render()}
// Reuse simple render for brevity - structural differences remain but mobile-compact
function renderA(){return '<div class=card><div style="font-weight:700">Variant A — Queue-first (phone)</div><div class=muted style="font-size:.8rem;color:#aebdca">Sidebar becomes bottom nav. Queue on top, detail drawer below.</div></div>'+cards()+queue()+detailDrawer()}
function renderB(){return '<div class=card style="background:#111a23"><div style="font-weight:700">Variant B — Overview-first (phone) ★ recommended base</div><div style="font-size:.8rem;color:#aebdca">Overview ClaimTags first, then queue, then full detail with policy gate and PaymentException rail as card.</div></div>'+cards()+queue()+detailFull()}
function renderC(){return '<div class=card><div style="font-weight:700">Variant C — Commander compact</div><div style="font-size:.8rem;color:#aebdca">Queue + detail stacked, rail collapsed into expandable cards for exceptions and investigation.</div></div>'+cards()+queue()+detailFull()+'<div class=card><strong>PaymentExceptions (rail collapsed to card)</strong><br>PX-014 blocks RC-0824 — <span class=tag block>awaiting evidence</span> — customer debited INR 499.00 — no capture yet</div>'}
function cards(){return '<div class=grid><div class=card style="border-left:4px solid #e0a847"><div style="font-size:.7rem;color:#aebdca">Revenue at risk</div><div style="font-size:1.2rem">'+money(m.overview.risk)+'</div><span class=tag est>ESTIMATED</span></div><div class=card style="border-left:4px solid #e0a847"><div style="font-size:.7rem;color:#aebdca">Estimated recoverable</div><div style="font-size:1.2rem">'+money(m.overview.est)+'</div><span class=tag est>ESTIMATED</span><div style="font-size:.7rem;color:#aebdca">top LF-241 only</div></div><div class=card style="border-left:4px solid #61c795"><div style="font-size:.7rem;color:#aebdca">Actual recovered</div><div style="font-size:1.2rem">'+money(m.overview.rec)+'</div><span class=tag test>TEST MODE</span></div><div class=card style="border-left:4px solid #7eb2e0"><div style="font-size:.7rem;color:#aebdca">Simulated recovery</div><div style="font-size:1.2rem">'+money(m.overview.sim)+'</div><span class=tag sim>SIMULATED</span></div></div>'}
function queue(){return '<div class=card><div style="display:flex;justify-content:space-between"><strong>Recovery queue</strong><span style="font-size:.8rem;color:#aebdca">'+m.overview.open+' open • escalations → exceptions → eligible by EV</span></div><table style="margin-top:8px"><thead><tr><th>Case</th><th>Status</th><th>Action</th></tr></thead><tbody>'+m.queue.map(q=>'<tr><td><strong>'+q.id+'</strong><br><span style="font-size:.8rem">'+money(q.amt)+' • '+q.err+' • '+q.age+'</span></td><td>'+pill(q.st)+(q.exp?'<br><span style="font-size:.75rem">exp '+money(q.exp)+'</span>':'')+'</td><td>'+(q.block?'<span class=tag block>BLOCKED</span>':q.st==='eligible'?'<button style="background:#1a3a2a;border:1px solid #2a6b4a;color:#92e5b7;padding:6px 8px;border-radius:6px">Approve</button>':'<span style="font-size:.8rem;color:#aebdca">View</span>')+'</td></tr>').join('')+'</tbody></table></div>'}
function detailDrawer(){return '<div class=card><strong>RecoveryCase RC-0829 — detail drawer</strong> (obligation ORD-98231 verified • CUST-882 consent ✓ • 1/3 contacts)<br>'+pill('eligible')+' <span class=tag est>EST exp '+money(78500)+'</span><div style="margin-top:8px;padding:8px;background:#1a2e22;border:1px solid #2a6b4a;border-radius:8px;font-size:.85rem"><strong>Policy gate</strong> v7 — allowed: payment_link, contact — blocked: retry (prior provider failure) — human approval required</div><div style="font-size:.85rem;margin-top:8px">Audit: payment.failed card insufficient_funds ev_29X → decision v7/v2 → mock email draft (MOCK) needs approval • idempotent • 24h window</div></div>'}
function detailFull(){return '<div class=card><strong>RecoveryCase RC-0829</strong> — ORD-98231 verified • CUST-882 1/3 contacts '+pill('eligible')+' <span class=tag est>EST '+money(78500)+'</span><div style="margin-top:8px;display:grid;gap:8px"><div style="padding:8px;background:#1a2e22;border:1px solid #2a6b4a;border-radius:8px;font-size:.85rem"><strong>Policy gate</strong> — allowed payment_link, contact; blocked retry; consent ✓; quiet-hours OK; approval needed</div><div style="padding:8px;background:#2a1a1a;border:1px solid #7a3a38;border-radius:8px;font-size:.85rem;display:none" id="blk">PaymentException blocks contact — resolve PX first</div><div style="background:#0f1923;border:1px solid #33414e;border-radius:8px;padding:8px"><div style="font-weight:600">contact — mock email (MOCK)</div><div style="font-size:.8rem;color:#aebdca">Subject: Complete your payment for ORD-98231<br>Pay now [mock link] or reply pay / promise / help / opt-out. No real delivery.</div><button style="width:100%;margin-top:8px;padding:10px;background:#61c795;color:#0e141c;border:none;border-radius:8px;font-weight:700">Approve contact</button><div style="font-size:.7rem;color:#aebdca">Records AuditEvent • idempotent • quiet-hours checked</div></div></div></div>'}
function render(){
 const v=getV(); const app=document.getElementById('app');
 if(v==='A') app.innerHTML=renderA(); else if(v==='B') app.innerHTML=renderB(); else app.innerHTML=renderC();
 document.getElementById('label').innerHTML='Variant <strong>'+v+'</strong> — '+(v==='A'?'Queue-first':v==='B'?'Overview-first ★':'Commander')+' <span style="color:#aebdca;font-size:.75rem">'+(v==='A'?'ops speed':v==='B'?'demo clarity':'power context')+'</span>';
 document.getElementById('bnav').innerHTML=['Overview','Queue','Detail','Exceptions','Policy','Investigation','Evaluation'].map(n=>'<a class='+(n==='Queue'&&v==='A'||n==='Overview'&&v==='B'?'active':'')+'>'+n+'</a>').join('');
 document.getElementById('prev').onclick=()=>setV(v==='A'?'C':v==='B'?'A':'B');
 document.getElementById('next').onclick=()=>setV(v==='A'?'B':v==='B'?'C':'A');
}
 document.addEventListener('keydown',e=>{if(e.target.matches('input,textarea,[contenteditable]'))return; if(e.key==='ArrowLeft')document.getElementById('prev').click(); if(e.key==='ArrowRight')document.getElementById('next').click()});
 render();
</script></body></html>
"""


@router.get("/prototype/mobile", response_class=HTMLResponse, include_in_schema=False)
def mobile_prototype(request: Request) -> str:
    # variant is handled client-side via ?variant=, keep server simple
    return MOBILE_HTML
