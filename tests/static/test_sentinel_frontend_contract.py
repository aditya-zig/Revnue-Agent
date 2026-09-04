import html as html_module
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = ROOT / "app/templates/dashboard.html"
STOREFRONT = ROOT / "app/templates/storefront.html"
SENTINEL_JS = ROOT / "app/static/js/sentinel.js"
SENTINEL_API = ROOT / "app/static/js/sentinel-api.js"
SENTINEL_CSS = ROOT / "app/static/css/sentinel.css"
GUIDED_SANDBOX_JS = ROOT / "app/static/js/guided-sandbox.js"


def test_dashboard_uses_real_sentinel_shell() -> None:
    html = html_module.unescape(DASHBOARD.read_text(encoding="utf-8"))
    assert "/static/css/sentinel.css" in html
    assert "/static/js/sentinel.js" in html
    for label in (
        "Home",
        "Payments",
        "Incidents",
        "Recoveries",
        "Policy & Safety",
        "Exceptions",
        "Outcomes",
        "Evaluation",
    ):
        assert label in html
    assert 'href="/judge"' not in html
    assert "Judge mode" not in html
    assert "Judge Mode" not in html
    assert "Razorpay Buildathon Demo" not in html
    assert "Hackathon Project" not in html
    assert 'href="/storefront"' in html


def test_frontend_uses_incident_backend_as_state_authority() -> None:
    javascript = SENTINEL_JS.read_text(encoding="utf-8")
    api = SENTINEL_API.read_text(encoding="utf-8")
    combined = f"{javascript}\n{api}"

    for endpoint_fragment in (
        "/incidents",
        "/control",
        "/investigate",
        "/approve",
        "/execute",
        "/replay/run",
    ):
        assert endpoint_fragment in combined

    for fake_progression in (
        "state.phase",
        "state.approved",
        "approved=true",
        "data-advance",
    ):
        assert fake_progression not in javascript

    assert "Removed before AI ranking" in javascript
    assert "Actual recovered" in javascript
    assert "awaiting provider" in javascript.lower()


def test_sentinel_styles_cover_desktop_and_mobile_layouts() -> None:
    css = SENTINEL_CSS.read_text(encoding="utf-8")
    assert "grid-template-columns:var(--sidebar) minmax(0,1fr)" in css
    assert "@media(max-width:820px)" in css
    assert "@media(max-width:480px)" in css


def test_storefront_has_top_left_back_link_and_real_checkout_script() -> None:
    html = STOREFRONT.read_text(encoding="utf-8")
    assert 'class="back-link"' in html
    assert 'href="/"' in html
    assert "checkout.razorpay.com/v1/checkout.js" in html
    assert "/static/js/storefront.js" in html


def test_landing_has_one_primary_try_it_yourself_entry() -> None:
    html = DASHBOARD.read_text(encoding="utf-8")
    assert html.count("Try it for yourself →") == 1
    assert "/static/js/guided-sandbox.js" in html
    assert "Catch payment incidents before they become lost revenue." in html
    assert "SIMULATED PRODUCT STORY" in html


def test_landing_story_never_claims_provider_verified_recovery() -> None:
    javascript = GUIDED_SANDBOX_JS.read_text(encoding="utf-8")
    story = javascript.split("function startStory()", 1)[1].split(
        "function openSandbox()", 1
    )[0]

    assert "SIMULATED PRODUCT STORY" in story
    assert "ESTIMATED AT RISK" in story
    assert "₹0 ACTUAL RECOVERED" in story
    assert "Awaiting provider evidence" in story
    assert "PROVIDER VERIFIED" not in story
    assert ">RECOVERED<" not in story
    assert "Provider outcome verified" not in story


def test_guided_sandbox_is_bound_to_exact_replay_and_incident() -> None:
    javascript = GUIDED_SANDBOX_JS.read_text(encoding="utf-8")

    assert "runId" in javascript
    assert "incidentId" in javascript
    assert "replayId" in javascript
    assert "replay_id=" in javascript
    assert "replay.incident_id" in javascript
    assert "replay.run_id" in javascript
    assert "return incidents[0]" not in javascript
    assert "findIncident" not in javascript
    assert "baselineRecovered" not in javascript
    assert "recovered > state.baselineRecovered" not in javascript
    assert "/control" in javascript


def test_guided_sandbox_auto_investigates_and_uses_backend_policy() -> None:
    html = DASHBOARD.read_text(encoding="utf-8")
    javascript = GUIDED_SANDBOX_JS.read_text(encoding="utf-8")

    assert "Test purchase observed" not in javascript
    assert "Continue after test purchase" not in javascript
    assert 'id="generateAnalysis"' not in html
    assert "Generate analysis" not in javascript
    assert "/investigate" in javascript
    assert "Incident ready for review" in javascript
    assert "policyAllowed" in html
    assert "policyBlocked" in html
    assert "recommendationAction" in html
    assert "recommendationReason" in html
    assert "case_recommendations" in javascript
    assert "allowed_actions" in javascript
    assert "blocked_actions" in javascript
    assert "recommended_action" in javascript


def test_guided_sandbox_waits_on_exact_incident_provider_outcome() -> None:
    html = DASHBOARD.read_text(encoding="utf-8")
    javascript = GUIDED_SANDBOX_JS.read_text(encoding="utf-8")

    assert "Approval is not recovery." in javascript
    assert "actual_recovered_amount_paise" in javascript
    assert "awaiting_provider_evidence" in javascript
    assert 'id="continueExploring"' in html
    assert "Continue exploring" in html
    assert "providerVerified(control" in javascript
    assert "test_mode_value" not in javascript


def test_guided_sandbox_exposes_persisted_audit_and_reproducible_evaluation() -> None:
    html = DASHBOARD.read_text(encoding="utf-8")
    javascript = GUIDED_SANDBOX_JS.read_text(encoding="utf-8")

    assert 'data-view="audit"' in html
    assert 'data-view="evaluation"' in html
    assert 'id="auditList"' in html
    assert 'id="evaluationList"' in html
    assert "/evaluations/reproducible" in javascript
    assert "SIMULATED EVALUATION" in html
    assert "incident.audit" in javascript or ".audit" in javascript


def test_guided_sandbox_keeps_provider_backed_state_authority() -> None:
    assert GUIDED_SANDBOX_JS.exists(), "guided sandbox module must be shipped"
    javascript = GUIDED_SANDBOX_JS.read_text(encoding="utf-8")

    for endpoint_fragment in (
        "/replay/start",
        "/incidents/",
        "/investigate",
        "/control",
        "/approve",
        "/execute",
        "/evaluations/reproducible",
    ):
        assert endpoint_fragment in javascript

    assert "/replay/run?scenario=primary" not in javascript
    assert "providerVerified" in javascript
    assert "Approval is not recovery." in javascript
    assert "state.approved" not in javascript
    assert "approved=true" not in javascript
