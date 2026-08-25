import csv
from io import StringIO

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from simulator.generator import generate_csv


@pytest.fixture
def app(database_url):
    return create_app(database_url=database_url)


@pytest.mark.asyncio
async def test_generated_synthetic_corpus_imports_more_than_500_events(app):
    content = generate_csv()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        imported = await client.post(
            "/api/v1/data/import",
            content=content,
            headers={"Content-Type": "text/csv"},
        )
        cases = await client.get("/api/v1/cases")

    rows = list(csv.DictReader(StringIO(content)))
    case_ids = {case["case_id"] for case in cases.json()}

    assert imported.status_code == 201
    assert imported.json() == {"imported": len(rows), "duplicates": 0}
    assert len(rows) >= 500
    assert "case_demo_hard_decline" in case_ids
    assert "case_demo_provider_failure" in case_ids
    assert "case_demo_opt_out" in case_ids
    assert "case_demo_promise" in case_ids
