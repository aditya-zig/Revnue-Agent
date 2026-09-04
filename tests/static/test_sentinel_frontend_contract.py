from html import unescape
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = ROOT / "app/templates/dashboard.html"
STOREFRONT = ROOT / "app/templates/storefront.html"
SENTINEL_JS = ROOT / "app/static/js/sentinel.js"
SENTINEL_API = ROOT / "app/static/js/sentinel-api.js"
SENTINEL_CSS = ROOT / "app/static/css/sentinel.css"
GUIDED_SANDBOX_JS = ROOT / "app/static/js/guided-sandbox.js"


def test_dashboard_uses_real_sentinel_shell() -> None:
    html = unescape(DASHBOARD.read_text(encoding="utf-8"))
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
    assert 'href="/judge"' in html
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


def test_guided_sandbox_keeps_provider_backed_state_authority() -> None:
    assert GUIDED_SANDBOX_JS.exists(), "guided sandbox module must be shipped"
    javascript = GUIDED_SANDBOX_JS.read_text(encoding="utf-8")

    for endpoint_fragment in (
        "/storefront",
        "/replay/start",
        "/incidents/",
        "/investigate",
        "/approve",
        "/execute",
        "/dashboard",
    ):
        assert endpoint_fragment in javascript

    assert "/replay/run?scenario=primary" not in javascript
    assert "test_mode_value" in javascript
    assert "providerVerified" in javascript
    assert "Approval is not recovery." in javascript
    assert "state.approved" not in javascript
    assert "approved=true" not in javascript
