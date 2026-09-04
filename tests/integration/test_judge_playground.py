import httpx
import pytest

from app.main import create_app


@pytest.mark.asyncio
async def test_judge_redirects_to_primary_product(tmp_path):
    app = create_app(database_url=f"sqlite:///{tmp_path / 'judge.db'}")
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        follow_redirects=False,
    ) as client:
        response = await client.get("/judge")

    assert response.status_code == 307
    assert response.headers["location"] == "/"
