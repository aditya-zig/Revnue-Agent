import httpx
import pytest

from app.finding_analysis import OpenRouterProvider, OpenRouterProviderError


def test_openrouter_accepts_text_part_content(monkeypatch):
    def fake_post(url, **kwargs):
        return httpx.Response(
            200,
            json={
                "id": "gen_parts",
                "model": "free/test-model",
                "choices": [
                    {
                        "message": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        '{"hypotheses":["Method outage may be temporary."],'
                                        '"next_validation_steps":["Compare a later cohort."]}'
                                    ),
                                }
                            ]
                        }
                    }
                ],
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)

    completion = OpenRouterProvider(api_key="test-key").generate({"support": 10})

    assert completion.resolved_model == "free/test-model"
    assert "Method outage" in completion.output


def test_openrouter_accepts_parsed_object(monkeypatch):
    def fake_post(url, **kwargs):
        return httpx.Response(
            200,
            json={
                "id": "gen_parsed",
                "model": "free/test-model",
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "parsed": {
                                "hypotheses": ["The issue may be cohort-specific."],
                                "next_validation_steps": ["Compare the next detector run."],
                            },
                        }
                    }
                ],
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)

    completion = OpenRouterProvider(api_key="test-key").generate({"support": 10})

    assert "cohort-specific" in completion.output


def test_openrouter_surfaces_provider_error_inside_success_http_response(monkeypatch):
    def fake_post(url, **kwargs):
        return httpx.Response(
            200,
            json={
                "error": {
                    "code": 429,
                    "message": "provider rate limited",
                }
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)

    with pytest.raises(OpenRouterProviderError) as error:
        OpenRouterProvider(api_key="test-key").generate({"support": 10})

    assert error.value.reason == "rate_limited"
    assert error.value.status_code == 429


def test_openrouter_request_enables_response_healing(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(kwargs)
        return httpx.Response(
            200,
            json={
                "id": "gen_healing",
                "model": "free/test-model",
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"hypotheses":["h"],'
                                '"next_validation_steps":["s"]}'
                            )
                        }
                    }
                ],
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)

    OpenRouterProvider(api_key="test-key").generate({"support": 10})

    assert captured["json"]["plugins"] == [{"id": "response-healing"}]
    assert captured["json"]["response_format"]["type"] == "json_schema"
    assert captured["json"]["provider"]["data_collection"] == "deny"
