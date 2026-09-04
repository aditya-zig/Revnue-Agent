import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


@pytest.fixture
def app(database_url):
    return create_app(database_url=database_url, webhook_secret="test-secret")


@pytest.mark.asyncio
async def test_distinct_guided_replays_return_exact_isolated_incidents(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        run_a = await client.post(
            "/api/v1/replay/start",
            params={"replay_id": "guided_browser_a", "seed": 47},
        )
        run_b = await client.post(
            "/api/v1/replay/start",
            params={"replay_id": "guided_browser_b", "seed": 47},
        )

        assert run_a.status_code == 201
        assert run_b.status_code == 201
        payload_a = run_a.json()
        payload_b = run_b.json()

        assert payload_a["run_id"] != payload_b["run_id"]
        assert payload_a["incident_id"] != payload_b["incident_id"]
        assert payload_a["replay_id"] == "guided_browser_a"
        assert payload_b["replay_id"] == "guided_browser_b"

        incident_a = await client.get(f"/api/v1/incidents/{payload_a['incident_id']}")
        incident_b = await client.get(f"/api/v1/incidents/{payload_b['incident_id']}")

    assert incident_a.status_code == 200
    assert incident_b.status_code == 200
    assert incident_a.json()["incident_id"] == payload_a["incident_id"]
    assert incident_b.json()["incident_id"] == payload_b["incident_id"]
    assert incident_a.json()["cohort_filter"]["replay_id"] == "guided_browser_a"
    assert incident_b.json()["cohort_filter"]["replay_id"] == "guided_browser_b"
    assert incident_a.json()["cohort_filter"]["run_id"] == payload_a["run_id"]
    assert incident_b.json()["cohort_filter"]["run_id"] == payload_b["run_id"]


@pytest.mark.asyncio
async def test_global_incident_listing_does_not_define_guided_run_identity(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        run_a = await client.post(
            "/api/v1/replay/start",
            params={"replay_id": "guided_listing_a", "seed": 47},
        )
        run_b = await client.post(
            "/api/v1/replay/start",
            params={"replay_id": "guided_listing_b", "seed": 47},
        )
        listed = await client.get("/api/v1/incidents")

    assert run_a.status_code == 201
    assert run_b.status_code == 201
    assert listed.status_code == 200
    listed_ids = {row["incident_id"] for row in listed.json()}
    assert run_a.json()["incident_id"] in listed_ids
    assert run_b.json()["incident_id"] in listed_ids
    assert run_a.json()["incident_id"] != run_b.json()["incident_id"]
