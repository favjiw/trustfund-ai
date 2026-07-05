import json
import logging

from google import genai
from google.genai import types
from pydantic import BaseModel, Field, ValidationError

from app.models import Confidence, Fairness

logger = logging.getLogger(__name__)


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


class LLMClientError(Exception):
    """Gagal mendapatkan atau mem-parsing hasil valid dari LLM."""


class GeminiClient:
    """Wrapper tipis di atas google-genai untuk memaksa output JSON terstruktur."""

    def __init__(self, api_key: str, model: str) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model

    def assess_rab(self, system_instruction: str, prompt: str) -> LLMValidationResult:
        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    response_schema=LLMValidationResult,
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
            return LLMValidationResult.model_validate(data)
        except ValidationError as exc:
            raise LLMClientError(f"Respons Gemini tidak sesuai schema: {exc}") from exc
