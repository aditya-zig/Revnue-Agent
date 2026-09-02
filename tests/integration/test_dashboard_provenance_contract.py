import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


@pytest.fixture
def app(database_url):
    return create_app(database_url=database_url)


@pytest.mark.asyncio
async def test_dashboard_frontend_preserves_claim_provenance_copy(app):
    """Keep demo-facing claim boundaries visible as the dashboard evolves."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        page = await client.get("/")
        frontend = await client.get("/static/js/app.js")

    assert page.status_code == 200
    assert frontend.status_code == 200

    # KPI claim tags visible in the server-rendered shell.
    assert "Estimated Recoverable" in page.text
    assert ">ESTIMATED<" in page.text
    assert "Actual Recovered" in page.text
    assert ">TEST MODE<" in page.text

    # Evaluation values must remain explicitly synthetic in the rendered UI code.
    assert 'tag("SIMULATED")' in frontend.text
    assert "Simulation only" in frontend.text
    assert "These values do not measure merchant recovery or provider outcomes." in frontend.text

    # The two monetary provenance claims must never be presented as production revenue.
    assert "Recorded Outcome amount in Test Mode" in frontend.text
    assert "Single top persisted LeakFinding" in frontend.text

    # Headline must remain evidence-disciplined and avoid unqualified revenue claims.
    assert "Investigate recoverable failures with evidence" in page.text
    assert "Recover revenue with evidence" not in page.text
