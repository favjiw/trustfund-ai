import io

import pytest
from PIL import Image

from app.models import Confidence, NotaItem, ParseNotaResponse
from app.services.llm_client import LLMClientError
from app.services.nota_parser import InvalidImageError, NotaParseError, NotaParser


class FakeLLMClient:
    def __init__(self, result: ParseNotaResponse | None = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.last_image: tuple[bytes, str] | None = None
        self.last_prompt: str | None = None

    def generate_structured(self, system_instruction, prompt, schema, image=None, temperature=0.2):
        self.last_image = image
        self.last_prompt = prompt
        if self._error is not None:
            raise self._error
        return self._result


def make_jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buf, format="JPEG")
    return buf.getvalue()


def make_result() -> ParseNotaResponse:
    return ParseNotaResponse(
        raw_text="TOKO JAYA\nSemen 50kg 2 x 70000 = 140000\nTOTAL 140000",
        items=[
            NotaItem(name="Semen 50kg", quantity=2, unit_price=70000, subtotal=140000, confidence=Confidence.TINGGI)
        ],
        detected_total=140000,
    )


def test_parse_sukses_meneruskan_gambar_dan_hints():
    llm = FakeLLMClient(result=make_result())
    parser = NotaParser(llm_client=llm)

    result = parser.parse(make_jpeg(), hint_items=["Semen 50kg", "Pasir"])

    assert result.detected_total == 140000
    assert result.items[0].name == "Semen 50kg"
    assert llm.last_image is not None
    assert llm.last_image[1] == "image/jpeg"
    assert "Semen 50kg" in llm.last_prompt


def test_bukan_gambar_error_400_style():
    parser = NotaParser(llm_client=FakeLLMClient(result=make_result()))
    with pytest.raises(InvalidImageError):
        parser.parse(b"bukan gambar sama sekali")


def test_llm_gagal_jadi_nota_parse_error():
    parser = NotaParser(llm_client=FakeLLMClient(error=LLMClientError("model vision tidak tersedia")))
    with pytest.raises(NotaParseError):
        parser.parse(make_jpeg())
