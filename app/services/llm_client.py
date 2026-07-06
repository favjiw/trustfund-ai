import base64
import json
import logging
import re
from typing import TypeVar

import httpx
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


def _schema_instruction(schema: type[BaseModel]) -> str:
    return (
        "Balas HANYA dengan satu objek JSON valid (tanpa markdown, tanpa teks lain) "
        "yang sesuai JSON schema berikut:\n"
        + json.dumps(schema.model_json_schema(), ensure_ascii=False)
    )


def _extract_json(text: str) -> str:
    """Ambil objek JSON dari respons model (buang code fence / teks pembungkus)."""
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    if text.startswith("{"):
        return text
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


class LLMClient:
    """Wrapper untuk API chat completions OpenAI-compatible (Sumopod/DeepSeek).

    Memaksa output JSON via response_format={"type":"json_object"} + instruksi
    schema di prompt, lalu memvalidasi dengan Pydantic.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: float = 60.0,
        max_tokens: int = 4096,
        supports_vision: bool = False,
        client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._max_tokens = max_tokens
        self._supports_vision = supports_vision
        self._client = client or httpx.Client(timeout=timeout_seconds)

    def _chat(self, messages: list[dict], temperature: float) -> str:
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": self._max_tokens,
            "response_format": {"type": "json_object"},
        }
        try:
            response = self._client.post(
                f"{self._base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:300]
            raise LLMClientError(f"Panggilan LLM gagal (HTTP {exc.response.status_code}): {body}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMClientError(f"Panggilan LLM gagal: {exc}") from exc

        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMClientError(f"Respons LLM tak terduga: {str(data)[:300]}") from exc

    def generate_structured(
        self,
        system_instruction: str,
        prompt: str,
        schema: type[T],
        image: tuple[bytes, str] | None = None,
        temperature: float = 0.2,
    ) -> T:
        """Panggil LLM dan paksa output JSON sesuai `schema` (Pydantic).

        `image`: pasangan (bytes, mime_type) opsional untuk input multimodal
        (dipakai vision fallback OCR). Hanya dikirim bila model mendukung vision.
        """
        system_message = f"{system_instruction}\n\n{_schema_instruction(schema)}"
        messages: list[dict] = [{"role": "system", "content": system_message}]

        if image is not None:
            if not self._supports_vision:
                raise LLMClientError(
                    f"Model '{self._model}' tidak mendukung input gambar "
                    "(set LLM_SUPPORTS_VISION=true bila model mendukung)."
                )
            image_bytes, mime_type = image
            b64 = base64.b64encode(image_bytes).decode("ascii")
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}},
                    ],
                }
            )
        else:
            messages.append({"role": "user", "content": prompt})

        raw_text = self._chat(messages, temperature)
        if not raw_text or not raw_text.strip():
            raise LLMClientError("Respons LLM kosong")

        try:
            parsed = json.loads(_extract_json(raw_text))
        except json.JSONDecodeError as exc:
            raise LLMClientError(f"Respons LLM bukan JSON valid: {exc}") from exc

        try:
            return schema.model_validate(parsed)
        except ValidationError as exc:
            raise LLMClientError(f"Respons LLM tidak sesuai schema: {exc}") from exc

    def assess_rab(self, system_instruction: str, prompt: str) -> LLMValidationResult:
        return self.generate_structured(system_instruction, prompt, LLMValidationResult)

    def extract_search_keywords(self, items: list[tuple[str, str]]) -> dict[str, str]:
        """items = daftar (id, nama). Kembalikan {id: keyword}. Melempar
        LLMClientError bila gagal (pemanggil menyediakan fallback heuristik)."""
        lines = "\n".join(f"- id={item_id} | nama=\"{name}\"" for item_id, name in items)
        prompt = f"Buat keyword pencarian untuk item berikut:\n{lines}"
        result = self.generate_structured(_KEYWORD_SYSTEM_INSTRUCTION, prompt, LLMKeywordResult)
        return {kw.id: kw.keyword.strip() for kw in result.keywords if kw.keyword.strip()}
