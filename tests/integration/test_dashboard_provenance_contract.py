import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


@pytest.fixture
def app(database_url):
    return create_app(database_url=database_url)


@pytest.mark.asyncio
async def test_dashboard_frontend_preserves_claim_provenance_copy(app):
    """Keep demo-facing claim boundaries visible as the operator console evolves."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        page = await client.get("/")
        frontend = await client.get("/static/js/sentinel-console.js")

    assert page.status_code == 200
    assert frontend.status_code == 200

    assert "SIMULATED DEMO DATA" in page.text
    assert "Test Mode" in page.text
    assert "Manual Refresh" not in page.text

    # Monetary and evaluation claims remain visibly qualified in the operator code.
    assert 'claimTag("ESTIMATED")' in frontend.text
    assert 'claimTag("SIMULATED")' in frontend.text
    assert "Razorpay Test Mode recovered" in frontend.text
    assert "No provider evidence → no recovered claim" in frontend.text
    evaluation_copy = (
        "These figures are simulated evaluation evidence, "
        "not production merchant performance."
    )
    assert evaluation_copy in frontend.text
    assert "Evaluation is a deterministic sandbox comparison." in frontend.text

    # The model and UI cannot turn estimates into provider facts.
    assert "AI assessment · advisory only" in frontend.text
    assert "does not treat a model hypothesis as provider fact" in frontend.text
    assert "No production money moves." in frontend.text
