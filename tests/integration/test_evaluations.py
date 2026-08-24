import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


@pytest.fixture
def app(database_url):
    return create_app(database_url=database_url)


@pytest.mark.asyncio
async def test_baseline_evaluation_is_deterministic_for_a_seed(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/evaluations/baseline?seed=7")
        second = await client.post("/api/v1/evaluations/baseline?seed=7")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    result = first.json()
    assert result["seed"] == 7
    assert result["case_count"] == 20
    assert result["recovered_amount"] > 0
    assert result["contact_cost"] > 0
    assert result["retry_cost"] > 0
    assert 0 < result["recovery_rate"] < 1
