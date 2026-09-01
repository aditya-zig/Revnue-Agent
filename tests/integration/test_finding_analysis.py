from datetime import UTC, datetime

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.db.tables import Decision, FindingAnalysis, RecoveryCase
from app.finding_analysis import OpenRouterCompletion, OpenRouterProviderError
from app.main import create_app


@pytest.fixture
def app(database_url):
    return create_app(
        database_url=database_url,
        policy_now=lambda: datetime(2026, 8, 24, 12, tzinfo=UTC),
    )


def finding_csv() -> str:
    header = (
        "event_id,event_type,payment_id,customer_id,amount,currency,method,status,"
        "error_code,error_reason,occurred_at,consent"
    )
    rows = [
        f"evt_{number},payment.failed,pay_{number},cust_{number},125000,INR,upi,failed,"
        f"BAD_REQUEST_ERROR,insufficient funds,2026-08-24T04:0{number}:00+00:00,true"
        for number in range(1, 4)
    ]
    rows.extend(
        f"captured_{number},payment.captured,captured_pay_{number},captured_cust_{number},"
        f"125000,INR,card,captured,,,2026-08-24T05:0{number}:00+00:00,true"
        for number in range(1, 4)
    )
    return "\n".join([header, *rows])


async def detect_one(client: AsyncClient) -> dict:
    await client.post("/api/v1/data/import", content=finding_csv())
    response = await client.post("/api/v1/findings/detect")
    assert response.status_code == 200
    return next(
        finding
        for finding in response.json()
        if finding["cohort_filter"] == {"dimension": "error_reason", "value": "insufficient funds"}
    )


@pytest.mark.asyncio
async def test_analysis_is_explicit_sanitized_idempotent_and_survives_detector_replacement(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        finding = await detect_one(client)
        finding_id = finding["finding_id"]

        assert (await client.get(f"/api/v1/findings/{finding_id}/analysis")).status_code == 404
        created = await client.post(
            f"/api/v1/findings/{finding_id}/analysis",
            json={"idempotency_key": "explain-001"},
        )
        repeated = await client.post(
            f"/api/v1/findings/{finding_id}/analysis",
            json={"idempotency_key": "explain-001"},
        )
        second = await client.post(
            f"/api/v1/findings/{finding_id}/analysis",
            json={"idempotency_key": "explain-002"},
        )

        assert created.status_code == 201
        assert repeated.status_code == 200
        assert repeated.json() == created.json()
        assert second.status_code == 201
        assert second.json()["analysis_id"] != created.json()["analysis_id"]

        saved = created.json()
        assert saved["impact_paise"] == 187500
        assert saved["recoverable_impact_paise"] == 93750
        assert saved["claim_tag"] == "ESTIMATED"
        assert saved["snapshot"]["attempted_value_paise"] == 375000
        assert saved["snapshot"]["failed_value_paise"] == 375000
        assert saved["result"]["external_model_generated"] is False
        assert saved["result"]["model_statement"] == "No external model generated this analysis."
        assert saved["result"]["observed_facts"]
        assert saved["result"]["hypotheses"]

        forbidden = {"raw_body", "customer_id", "event_id", "event_ids", "evidence"}

        def keys(value):
            if isinstance(value, dict):
                return set(value) | set().union(*(keys(item) for item in value.values()))
            if isinstance(value, list):
                return set().union(*(keys(item) for item in value))
            return set()

        assert not forbidden & keys(saved["snapshot"])
        assert not forbidden & keys(saved["result"])

        old_analysis = await client.get(f"/api/v1/findings/{finding_id}/analysis")
        assert old_analysis.status_code == 200
        assert old_analysis.json()["analysis_id"] == second.json()["analysis_id"]
        by_id = await client.get(f"/api/v1/finding-analyses/{created.json()['analysis_id']}")
        assert by_id.status_code == 200
        dashboard = await client.get("/api/v1/dashboard")
        assert (
            dashboard.json()["investigation"]["analysis"]["analysis_id"]
            == second.json()["analysis_id"]
        )

        replacement = await client.post("/api/v1/findings/detect")
        assert replacement.status_code == 200
        assert all(item["finding_id"] != finding_id for item in replacement.json())
        assert (await client.get(f"/api/v1/findings/{finding_id}/analysis")).status_code == 200


@pytest.mark.asyncio
class FakeOpenRouter:
    requested_model = "openrouter/free"

    def __init__(self, completion=None, error=None):
        self.completion = completion
        self.error = error
        self.snapshots = []

    def generate(self, snapshot):
        self.snapshots.append(snapshot)
        if self.error:
            raise self.error
        return self.completion


@pytest.mark.asyncio
async def test_analysis_uses_openrouter_strict_output_and_persists_metadata(database_url):
    provider = FakeOpenRouter(
        completion=OpenRouterCompletion(
            output='{"hypotheses":["The cohort may reflect a method-specific issue."],'
            '"next_validation_steps":["Compare the next detector run."]}',
            resolved_model="free/test-model",
            generation_id="gen_123",
            usage={"prompt_tokens": 120, "completion_tokens": 24, "total_tokens": 144},
        )
    )
    app = create_app(database_url=database_url, finding_analysis_provider=provider)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        finding = await detect_one(client)
        response = await client.post(
            f"/api/v1/findings/{finding['finding_id']}/analysis",
            json={"idempotency_key": "openrouter-valid"},
        )

    assert response.status_code == 201
    saved = response.json()
    assert saved["provider_metadata"] == {
        "provider": "openrouter",
        "requested_model": "openrouter/free",
        "resolved_model": "free/test-model",
        "provider_generation_id": "gen_123",
        "prompt_version": "openrouter-finding-analysis-v1",
        "usage": {"prompt_tokens": 120, "completion_tokens": 24, "total_tokens": 144},
        "tool_usage": {"requested": False, "used": False, "tools": []},
        "failure_reason": None,
        "fallback_used": False,
    }
    assert saved["result"]["external_model_generated"] is True
    assert saved["result"]["observed_facts"]
    assert saved["result"]["hypotheses"] == ["The cohort may reflect a method-specific issue."]
    assert saved["result"]["next_validation_steps"] == ["Compare the next detector run."]
    assert provider.snapshots and "customer_id" not in provider.snapshots[0]


@pytest.mark.asyncio
async def test_analysis_falls_back_on_invalid_output_and_provider_errors(database_url):
    for suffix, error in (
        ("invalid", None),
        ("rate-limit", OpenRouterProviderError("rate_limited", status_code=429)),
        ("no-model", OpenRouterProviderError("no_compatible_model")),
        ("provider", OpenRouterProviderError("provider_unavailable", status_code=503)),
    ):
        completion = (
            OpenRouterCompletion(
                output="not json", resolved_model="free/test-model", generation_id="gen_bad"
            )
            if error is None
            else None
        )
        provider = FakeOpenRouter(completion=completion, error=error)
        app = create_app(database_url=database_url, finding_analysis_provider=provider)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            finding = await detect_one(client)
            response = await client.post(
                f"/api/v1/findings/{finding['finding_id']}/analysis",
                json={"idempotency_key": f"openrouter-{suffix}"},
            )
        assert response.status_code == 201
        saved = response.json()
        assert saved["result"]["external_model_generated"] is False
        assert saved["result"]["model_statement"] == "No external model generated this analysis."
        assert saved["provider_metadata"]["fallback_used"] is True
        assert saved["provider_metadata"]["requested_model"] == "openrouter/free"
        assert saved["provider_metadata"]["failure_reason"]
        if suffix == "invalid":
            assert saved["provider_metadata"]["resolved_model"] == "free/test-model"
            assert saved["provider_metadata"]["provider_generation_id"] == "gen_bad"


@pytest.mark.asyncio
async def test_missing_openrouter_credentials_do_not_call_provider(database_url):
    app = create_app(database_url=database_url, openrouter_api_key="")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        finding = await detect_one(client)
        response = await client.post(
            f"/api/v1/findings/{finding['finding_id']}/analysis",
            json={"idempotency_key": "openrouter-missing-key"},
        )
    assert response.status_code == 201
    saved = response.json()
    assert saved["provider_metadata"]["failure_reason"] == "missing_credentials"
    assert saved["provider_metadata"]["fallback_used"] is True


def test_openrouter_request_is_strict_bounded_and_denies_provider_collection(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return httpx.Response(
            200,
            json={
                "id": "gen_request",
                "model": "free/test-model",
                "choices": [
                    {"message": {"content": '{"hypotheses":["h"],"next_validation_steps":["s"]}'}}
                ],
                "usage": {"total_tokens": 2},
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    from app.finding_analysis import OpenRouterProvider

    completion = OpenRouterProvider(api_key="secret-key").generate({"support": 3})

    assert completion.generation_id == "gen_request"
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer secret-key"
    assert captured["json"]["model"] == "openrouter/free"
    assert captured["json"]["max_tokens"] == 400
    assert captured["json"]["provider"] == {"allow_fallbacks": False, "data_collection": "deny"}
    assert "tools" not in captured["json"]
    assert captured["json"]["response_format"]["json_schema"]["strict"] is True
    assert (
        captured["json"]["response_format"]["json_schema"]["schema"]["additionalProperties"]
        is False
    )


@pytest.mark.asyncio
async def test_analysis_does_not_change_policy_or_action_execution(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        finding = await detect_one(client)
        analysis = await client.post(
            f"/api/v1/findings/{finding['finding_id']}/analysis",
            json={"idempotency_key": "policy-action-analysis"},
        )
        assert analysis.status_code == 201

        with app.state.session_factory() as session:
            session.get(RecoveryCase, "case_pay_1").state = "eligible"
            session.add(
                Decision(
                    decision_id="analysis_action_approval",
                    case_id="case_pay_1",
                    policy_version="v1",
                    model_version="v1",
                    allowed_actions=["contact"],
                    selected_action="contact",
                    expected_value=1,
                    reason_json={"approval": {"required": True, "granted": True}},
                )
            )
            session.commit()
        policy = await client.get("/api/v1/cases/case_pay_1/policy")
        action = await client.post(
            "/api/v1/cases/case_pay_1/actions",
            json={"action": "contact", "idempotency_key": "analysis-action"},
        )

    assert policy.status_code == 200
    assert "contact" in policy.json()["allowed_actions"]
    assert action.status_code == 201
    assert action.json()["action"] == "contact"
    with app.state.session_factory() as session:
        assert session.query(FindingAnalysis).count() == 1
