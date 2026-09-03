import httpx
import pytest

from app.main import create_app


@pytest.mark.asyncio
async def test_judge_playground_links_dashboard_and_storefront(tmp_path):
    app = create_app(database_url=f"sqlite:///{tmp_path / 'judge.db'}")
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/judge")

    assert response.status_code == 200
    assert "Find the leak. Recover safely." in response.text
    assert "Prove the outcome." in response.text
    assert 'href="/?autostart=1"' in response.text
    assert 'href="/"' in response.text
    assert 'href="/storefront"' in response.text
    assert "SIMULATED DEMO DATA" in response.text
    assert "ESTIMATED" in response.text
    assert "TEST MODE" in response.text
    assert "No production payment or production revenue claim" in response.text
