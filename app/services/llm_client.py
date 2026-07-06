import json
import logging
from typing import TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel, Field, ValidationError

from app.models import Confidence, Fairness

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMItemAssessment(BaseModel):
    id: str
    fairness: Fairness
    reason: str
    confidence: Confidence


class LLMValidationResult(BaseModel):
    overall_score: int = Field(ge=0, le=100)
    summary: str
    item_assessments: list[LLMItemAssessment]
    flags: list[str] = Field(default_factory=list)


class LLMKeyword(BaseModel):
    id: str
    keyword: str


class LLMKeywordResult(BaseModel):
    keywords: list[LLMKeyword]


_KEYWORD_SYSTEM_INSTRUCTION = (
    "Kamu membantu mencari harga di e-katalog pengadaan Indonesia (INAPROC). "
    "Untuk tiap item RAB, buat SATU keyword pencarian singkat (1-3 kata) berisi "
    "jenis barang generik tanpa merek, angka, atau satuan, agar katalog "
    "mengembalikan banyak produk pembanding. Contoh: 'Semen Portland 40kg Tiga "
    "Roda' -> 'semen portland'; 'Kursi roda standar' -> 'kursi roda'. Jawab HANYA "
    "JSON sesuai schema."
)


class LLMClientError(Exception):
    """Gagal mendapatkan atau mem-parsing hasil valid dari LLM."""


class GeminiClient:
    """Wrapper tipis di atas google-genai untuk memaksa output JSON terstruktur."""

    def __init__(self, api_key: str, model: str) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model

    def generate_structured(
        self,
        system_instruction: str,
        prompt: str,
        schema: type[T],
        image: tuple[bytes, str] | None = None,
    ) -> T:
        """Panggil Gemini dan paksa output JSON sesuai `schema` (Pydantic).

        `image`: pasangan (bytes, mime_type) opsional untuk input multimodal
        (dipakai vision fallback OCR).
        """
        if image is not None:
            image_bytes, mime_type = image
            contents: list | str = [
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                prompt,
            ]
        else:
            contents = prompt

        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    response_schema=schema,
                    temperature=0.2,
                ),
            )
        except Exception as exc:  # panggilan API eksternal, tangkap semua kegagalan
            raise LLMClientError(f"Panggilan Gemini gagal: {exc}") from exc

        raw_text = getattr(response, "text", None)
        if not raw_text:
            raise LLMClientError("Respons Gemini kosong")

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise LLMClientError(f"Respons Gemini bukan JSON valid: {exc}") from exc

        try:
            return schema.model_validate(data)
        except ValidationError as exc:
            raise LLMClientError(f"Respons Gemini tidak sesuai schema: {exc}") from exc

    def assess_rab(self, system_instruction: str, prompt: str) -> LLMValidationResult:
        return self.generate_structured(system_instruction, prompt, LLMValidationResult)

    def extract_search_keywords(self, items: list[tuple[str, str]]) -> dict[str, str]:
        """items = daftar (id, nama). Kembalikan {id: keyword}. Melempar
        LLMClientError bila gagal (pemanggil menyediakan fallback heuristik)."""
        lines = "\n".join(f"- id={item_id} | nama=\"{name}\"" for item_id, name in items)
        prompt = f"Buat keyword pencarian untuk item berikut:\n{lines}"
        result = self.generate_structured(_KEYWORD_SYSTEM_INSTRUCTION, prompt, LLMKeywordResult)
        return {kw.id: kw.keyword.strip() for kw in result.keywords if kw.keyword.strip()}
