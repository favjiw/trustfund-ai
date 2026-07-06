import io

from PIL import Image

from app.config import Settings
from app.models import Confidence, SuggestedItem
from app.services.llm_client import LLMClientError
from app.services.ocr_service import LLMOcrParseResult, LLMOcrVisionResult, OcrService


def make_image_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (100, 60), color=(255, 255, 255)).save(buffer, format="JPEG")
    return buffer.getvalue()


class FakeLLMClient:
    """Mengganti GeminiClient di test agar tidak memanggil API asli."""

    def __init__(self, result=None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.calls: list[dict] = []

    def generate_structured(self, system_instruction, prompt, schema, image=None):
        self.calls.append({"schema": schema, "image": image})
        if self._error is not None:
            raise self._error
        return self._result


def make_settings(**overrides) -> Settings:
    values = {"gemini_api_key": "dummy", "internal_token": "x"}
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_ocr_engine_unavailable_falls_back_to_vision():
    vision_result = LLMOcrVisionResult(
        raw_text="TOKO BANGUNAN\nSemen 50kg 2 x 65000 = 130000",
        suggested_items=[
            SuggestedItem(name="Semen 50kg", quantity=2, unit_price=65000, subtotal=130000, confidence=Confidence.SEDANG)
        ],
        detected_total=130000,
        warnings=[],
    )
    llm = FakeLLMClient(result=vision_result)
    service = OcrService(settings=make_settings(), llm_client=llm)
    # load() tidak dipanggil → engine None, mensimulasikan PaddleOCR gagal/absen

    response = service.assist(make_image_bytes(), hint_items=["Semen 50kg"])

    assert response.source == "vision_fallback"
    assert response.suggested_items[0].name == "Semen 50kg"
    assert response.detected_total == 130000
    assert llm.calls[0]["image"] is not None  # gambar benar-benar dikirim ke Gemini


def test_vision_fallback_disabled_returns_partial_not_error():
    llm = FakeLLMClient(result=None)
    service = OcrService(settings=make_settings(ocr_vision_fallback=False), llm_client=llm)

    response = service.assist(make_image_bytes())

    assert response.source == "paddleocr"
    assert response.suggested_items == []
    assert response.warnings  # ada penjelasan, bukan error keras
    assert llm.calls == []  # LLM tidak dipanggil sama sekali


def test_vision_fallback_failure_still_returns_200_style_partial():
    llm = FakeLLMClient(error=LLMClientError("Gemini down"))
    service = OcrService(settings=make_settings(), llm_client=llm)

    response = service.assist(make_image_bytes())

    assert response.source == "vision_fallback"
    assert response.suggested_items == []
    assert any("gagal" in w.lower() for w in response.warnings)


def test_paddle_text_structured_via_llm(monkeypatch):
    parse_result = LLMOcrParseResult(
        suggested_items=[
            SuggestedItem(name="Pasir", quantity=1, unit_price=None, subtotal=None, confidence=Confidence.RENDAH)
        ],
        detected_total=None,
        warnings=["harga pasir tidak terbaca"],
    )
    llm = FakeLLMClient(result=parse_result)
    service = OcrService(settings=make_settings(), llm_client=llm)

    service._engine = object()  # pura-pura engine siap
    monkeypatch.setattr(
        OcrService,
        "_extract_text",
        lambda self, image_bytes: "TOKO BANGUNAN JAYA\nPasir 1 rit\nTerima kasih",
    )

    response = service.assist(make_image_bytes())

    assert response.source == "paddleocr"
    assert response.raw_text.startswith("TOKO BANGUNAN")
    assert response.suggested_items[0].unit_price is None
    assert response.suggested_items[0].confidence == Confidence.RENDAH
    assert "harga pasir tidak terbaca" in response.warnings
