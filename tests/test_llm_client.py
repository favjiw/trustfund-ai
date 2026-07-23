import json

import httpx
import pytest

from app.services.llm_client import LLMClient, LLMClientError, LLMValidationResult


def _client(handler, **kwargs) -> LLMClient:
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url="http://llm.test")
    return LLMClient(
        api_key="sk-test",
        model="deepseek-v4-pro",
        base_url="http://llm.test/v1",
        client=http,
        **kwargs,
    )


def _completion(content: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def test_sends_openai_shape_and_parses_json():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        payload = {"overall_score": 90, "summary": "ok", "item_assessments": [], "flags": []}
        return httpx.Response(200, json=_completion(json.dumps(payload)))

    client = _client(handler)
    result = client.assess_rab("sys", "prompt")

    assert isinstance(result, LLMValidationResult)
    assert result.overall_score == 90
    assert captured["auth"] == "Bearer sk-test"
    assert captured["body"]["model"] == "deepseek-v4-pro"
    assert captured["body"]["response_format"] == {"type": "json_object"}
    # schema instruction diselipkan ke system message
    assert "JSON schema" in captured["body"]["messages"][0]["content"]


def test_strips_markdown_code_fence():
    fenced = "```json\n{\"overall_score\": 55, \"summary\": \"x\", \"item_assessments\": [], \"flags\": []}\n```"
    client = _client(lambda r: httpx.Response(200, json=_completion(fenced)))

    result = client.assess_rab("sys", "prompt")
    assert result.overall_score == 55


def test_http_error_raises_llm_client_error():
    client = _client(lambda r: httpx.Response(429, json={"error": "rate limit"}), max_retries=0)
    with pytest.raises(LLMClientError):
        client.assess_rab("sys", "prompt")


def test_retries_then_succeeds_on_transient_503(monkeypatch):
    monkeypatch.setattr("app.services.llm_client.time.sleep", lambda s: None)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, json={"error": "high demand"})
        payload = {"overall_score": 77, "summary": "ok", "item_assessments": [], "flags": []}
        return httpx.Response(200, json=_completion(json.dumps(payload)))

    client = _client(handler, max_retries=3, retry_backoff_seconds=0)
    result = client.assess_rab("sys", "prompt")
    assert result.overall_score == 77
    assert calls["n"] == 3  # 2 gagal + 1 sukses


def test_does_not_retry_on_auth_error(monkeypatch):
    monkeypatch.setattr("app.services.llm_client.time.sleep", lambda s: None)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, json={"error": "bad key"})

    client = _client(handler, max_retries=3)
    with pytest.raises(LLMClientError):
        client.assess_rab("sys", "prompt")
    assert calls["n"] == 1  # 401 tidak di-retry


def test_error_message_does_not_leak_response_body():
    secret_body = {"error": "Received API Key = sk-REALSECRET123"}
    client = _client(lambda r: httpx.Response(401, json=secret_body), max_retries=0)
    with pytest.raises(LLMClientError) as excinfo:
        client.assess_rab("sys", "prompt")
    assert "sk-REALSECRET123" not in str(excinfo.value)


def test_invalid_json_raises_llm_client_error():
    client = _client(lambda r: httpx.Response(200, json=_completion("bukan json sama sekali")))
    with pytest.raises(LLMClientError):
        client.assess_rab("sys", "prompt")


def test_vision_guard_when_model_text_only():
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json=_completion("{}"))

    client = _client(handler, supports_vision=False)
    with pytest.raises(LLMClientError, match="tidak mendukung input gambar"):
        client.generate_structured("sys", "prompt", LLMValidationResult, image=(b"\xff\xd8", "image/jpeg"))
    assert called is False  # tidak ada panggilan API yang sia-sia


def test_vision_sends_image_url_when_supported():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        payload = {"overall_score": 70, "summary": "ok", "item_assessments": [], "flags": []}
        return httpx.Response(200, json=_completion(json.dumps(payload)))

    client = _client(handler, supports_vision=True)
    client.generate_structured("sys", "prompt", LLMValidationResult, image=(b"\xff\xd8\xff", "image/jpeg"))

    content = captured["body"]["messages"][1]["content"]
    assert isinstance(content, list)
    assert any(part.get("type") == "image_url" for part in content)
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


class _Simple(LLMValidationResult):
    pass


def _tiny_png() -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_vision_model_dipakai_saat_ada_gambar():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        payload = {"overall_score": 80, "summary": "ok", "item_assessments": [], "flags": []}
        return httpx.Response(200, json=_completion(json.dumps(payload)))

    client = _client(
        handler,
        vision_model="gemini-2.5-flash",
        vision_base_url="http://gemini.test/v1beta/openai",
        vision_api_key="AIza-test",
    )
    client.generate_structured("sys", "prompt", _Simple, image=(_tiny_png(), "image/png"))

    # Request gambar dialihkan ke endpoint + key + model vision.
    assert captured["body"]["model"] == "gemini-2.5-flash"
    assert captured["url"].startswith("http://gemini.test/v1beta/openai/")
    assert captured["auth"] == "Bearer AIza-test"
    # Model vision (mis. gpt-5-nano) menolak temperature/max_tokens non-default.
    assert "temperature" not in captured["body"]
    assert "max_tokens" not in captured["body"]


def test_teks_tetap_pakai_model_utama_meski_vision_model_diisi():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        payload = {"overall_score": 80, "summary": "ok", "item_assessments": [], "flags": []}
        return httpx.Response(200, json=_completion(json.dumps(payload)))

    client = _client(handler, vision_model="gemini-2.5-flash", vision_base_url="http://gemini.test/v1")
    client.assess_rab("sys", "prompt")

    assert captured["body"]["model"] == "deepseek-v4-pro"


def test_gambar_tanpa_vision_model_dan_tanpa_dukungan_gagal_jelas():
    client = _client(lambda r: httpx.Response(200, json=_completion("{}")))
    with pytest.raises(LLMClientError, match="LLM_VISION_MODEL"):
        client.generate_structured("sys", "prompt", _Simple, image=(_tiny_png(), "image/png"))
