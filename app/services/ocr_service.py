import io
import logging

from pydantic import BaseModel, Field

from app.config import Settings
from app.models import OcrAssistResponse, SuggestedItem
from app.prompts.ocr_parse_prompt import (
    OCR_PARSE_SYSTEM_INSTRUCTION,
    OCR_VISION_SYSTEM_INSTRUCTION,
    build_ocr_parse_prompt,
    build_ocr_vision_prompt,
)
from app.services.llm_client import GeminiClient, LLMClientError

logger = logging.getLogger(__name__)

_MIME_BY_FORMAT = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "BMP": "image/bmp",
    "TIFF": "image/tiff",
}


class InvalidImageError(Exception):
    """Input bukan gambar yang valid."""


class LLMOcrParseResult(BaseModel):
    """Schema output Gemini saat merapikan teks OCR menjadi item terstruktur."""

    suggested_items: list[SuggestedItem] = Field(default_factory=list)
    detected_total: float | None = None
    warnings: list[str] = Field(default_factory=list)


class LLMOcrVisionResult(LLMOcrParseResult):
    """Schema output Gemini saat membaca gambar nota langsung (vision fallback)."""

    raw_text: str = ""


def sniff_image_mime(image_bytes: bytes) -> str:
    """Validasi bahwa bytes adalah gambar dan kembalikan MIME type-nya."""
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            img.verify()
            fmt = img.format
    except (UnidentifiedImageError, OSError) as exc:
        raise InvalidImageError("File yang dikirim bukan gambar yang valid") from exc

    return _MIME_BY_FORMAT.get(fmt or "", "image/jpeg")


class OcrService:
    """OCR assist: PaddleOCR (utama) + Gemini vision fallback, best-effort."""

    def __init__(self, settings: Settings, llm_client: GeminiClient) -> None:
        self._settings = settings
        self._llm_client = llm_client
        self._engine = None

    def load(self) -> None:
        """Muat model PaddleOCR SEKALI saat startup. Gagal → OCR nonaktif,
        service tetap jalan dengan vision fallback."""
        try:
            from paddleocr import PaddleOCR

            self._engine = PaddleOCR(
                use_angle_cls=True,
                lang=self._settings.ocr_lang,
                show_log=False,
            )
            logger.info("PaddleOCR dimuat (lang=%s)", self._settings.ocr_lang)
        except Exception as exc:
            self._engine = None
            logger.warning(
                "PaddleOCR tidak tersedia (%s). OCR assist akan bergantung pada "
                "vision fallback.",
                exc,
            )

    @property
    def ocr_ready(self) -> bool:
        return self._engine is not None

    def _extract_text(self, image_bytes: bytes) -> str:
        import numpy as np
        from PIL import Image

        with Image.open(io.BytesIO(image_bytes)) as img:
            np_img = np.array(img.convert("RGB"))

        result = self._engine.ocr(np_img, cls=True)
        lines: list[str] = []
        for page in result or []:
            for entry in page or []:
                text = entry[1][0] if len(entry) > 1 and entry[1] else ""
                if text:
                    lines.append(text)
        return "\n".join(lines)

    def _vision_fallback(self, image_bytes: bytes, mime_type: str, hint_items: list[str], warnings: list[str]) -> OcrAssistResponse:
        try:
            result = self._llm_client.generate_structured(
                OCR_VISION_SYSTEM_INSTRUCTION,
                build_ocr_vision_prompt(hint_items),
                LLMOcrVisionResult,
                image=(image_bytes, mime_type),
            )
        except LLMClientError as exc:
            logger.warning("Vision fallback gagal: %s", exc)
            warnings.append("OCR dan vision fallback gagal membaca nota; silakan isi form manual.")
            return OcrAssistResponse(
                source="vision_fallback", raw_text="", suggested_items=[], detected_total=None, warnings=warnings
            )

        return OcrAssistResponse(
            source="vision_fallback",
            raw_text=result.raw_text,
            suggested_items=result.suggested_items,
            detected_total=result.detected_total,
            warnings=warnings + result.warnings,
        )

    def assist(self, image_bytes: bytes, hint_items: list[str] | None = None) -> OcrAssistResponse:
        """Hasilkan SARAN isian form dari foto nota. Best-effort: selalu
        kembalikan hasil (mungkin parsial), tidak pernah melempar error keras
        kecuali input bukan gambar."""
        hint_items = hint_items or []
        mime_type = sniff_image_mime(image_bytes)
        warnings: list[str] = []

        raw_text = ""
        if self._engine is not None:
            try:
                raw_text = self._extract_text(image_bytes)
            except Exception as exc:
                logger.warning("PaddleOCR gagal mengekstrak teks: %s", exc)
                warnings.append("OCR utama gagal membaca gambar.")
        else:
            warnings.append("Engine OCR tidak tersedia.")

        if len(raw_text.strip()) < self._settings.ocr_min_text_len:
            if raw_text.strip():
                warnings.append("Teks hasil OCR terlalu pendek / sebagian tidak terbaca.")
            if self._settings.ocr_vision_fallback:
                return self._vision_fallback(image_bytes, mime_type, hint_items, warnings)
            warnings.append("Vision fallback nonaktif; silakan isi form manual.")
            return OcrAssistResponse(
                source="paddleocr", raw_text=raw_text, suggested_items=[], detected_total=None, warnings=warnings
            )

        try:
            parsed = self._llm_client.generate_structured(
                OCR_PARSE_SYSTEM_INSTRUCTION,
                build_ocr_parse_prompt(raw_text, hint_items),
                LLMOcrParseResult,
            )
        except LLMClientError as exc:
            logger.warning("Strukturisasi teks OCR gagal: %s", exc)
            warnings.append("Teks terbaca tetapi gagal disusun menjadi item; silakan isi form manual.")
            return OcrAssistResponse(
                source="paddleocr", raw_text=raw_text, suggested_items=[], detected_total=None, warnings=warnings
            )

        return OcrAssistResponse(
            source="paddleocr",
            raw_text=raw_text,
            suggested_items=parsed.suggested_items,
            detected_total=parsed.detected_total,
            warnings=warnings + parsed.warnings,
        )
