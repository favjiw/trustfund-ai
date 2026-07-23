import base64
import json
import logging
import re
import time
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

    _RETRIABLE_STATUS = {429, 500, 502, 503, 504}

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: float = 60.0,
        max_tokens: int = 4096,
        supports_vision: bool = False,
        vision_model: str = "",
        vision_base_url: str = "",
        vision_api_key: str = "",
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.5,
        client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._max_tokens = max_tokens
        self._supports_vision = supports_vision
        self._vision_model = vision_model.strip()
        # Endpoint/key vision boleh berbeda provider (mis. Gemini); kosong = ikut utama.
        self._vision_base_url = (vision_base_url.strip() or base_url).rstrip("/")
        self._vision_api_key = vision_api_key.strip() or api_key
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff_seconds
        self._client = client or httpx.Client(timeout=timeout_seconds)

    def _post_once(self, payload: dict, base_url: str, api_key: str) -> httpx.Response:
        return self._client.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )

    def _chat(self, messages: list[dict], temperature: float, use_vision: bool = False) -> str:
        base_url = self._vision_base_url if use_vision else self._base_url
        api_key = self._vision_api_key if use_vision else self._api_key
        payload = {
            "model": (self._vision_model or self._model) if use_vision else self._model,
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        # Model vision (mis. gpt-5-nano) menolak temperature != default dan bisa
        # menghabiskan max_tokens untuk reasoning hingga JSON terpotong — kirim
        # kedua parameter hanya untuk jalur teks.
        if not use_vision:
            payload["temperature"] = temperature
            payload["max_tokens"] = self._max_tokens

        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = self._post_once(payload, base_url, api_key)
                response.raise_for_status()
                data = response.json()
                break
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                last_error = LLMClientError(f"Panggilan LLM gagal (HTTP {status})")
                # Retry hanya untuk error transient; 4xx lain (mis. 401/400) langsung gagal.
                if status in self._RETRIABLE_STATUS and attempt < self._max_retries:
                    logger.warning("LLM HTTP %s, retry %d/%d...", status, attempt + 1, self._max_retries)
                    time.sleep(self._retry_backoff * (2**attempt))
                    continue
                raise last_error from exc
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_error = LLMClientError(f"Panggilan LLM gagal (jaringan): {type(exc).__name__}")
                if attempt < self._max_retries:
                    logger.warning("LLM error jaringan, retry %d/%d...", attempt + 1, self._max_retries)
                    time.sleep(self._retry_backoff * (2**attempt))
                    continue
                raise last_error from exc
            except (httpx.HTTPError, ValueError) as exc:
                raise LLMClientError(f"Panggilan LLM gagal: {type(exc).__name__}") from exc
        else:  # pragma: no cover - dijaga oleh raise di dalam loop
            raise last_error or LLMClientError("Panggilan LLM gagal")

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
        (dipakai parse-nota). Diteruskan ke model vision (LLM_VISION_MODEL) bila
        dikonfigurasi, atau ke model utama bila mendukung vision.
        """
        system_message = f"{system_instruction}\n\n{_schema_instruction(schema)}"
        messages: list[dict] = [{"role": "system", "content": system_message}]

        use_vision = image is not None
        if image is not None:
            # Jalur vision: model/endpoint vision khusus, atau model utama bila mendukung.
            if not self._vision_model and not self._supports_vision:
                raise LLMClientError(
                    f"Model '{self._model}' tidak mendukung input gambar. Set "
                    "LLM_VISION_MODEL ke model vision (mis. gemini-2.5-flash) atau "
                    "LLM_SUPPORTS_VISION=true bila model utama mendukung gambar."
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

        # Dua percobaan: bila output pertama melenceh dari schema (model kecil
        # kadang menukar enum/format), kirim balik pesan error agar model
        # mengoreksi jawabannya sendiri.
        last_error = ""
        for attempt in range(2):
            raw_text = self._chat(messages, temperature, use_vision=use_vision)
            if not raw_text or not raw_text.strip():
                raise LLMClientError("Respons LLM kosong")

            try:
                parsed = json.loads(_extract_json(raw_text))
            except json.JSONDecodeError as exc:
                last_error = f"Respons LLM bukan JSON valid: {exc}"
            else:
                try:
                    return schema.model_validate(parsed)
                except ValidationError as exc:
                    last_error = f"Respons LLM tidak sesuai schema: {exc}"

            if attempt == 0:
                logger.warning("Output LLM melenceh dari schema, retry dengan feedback: %s", last_error[:300])
                messages.append({"role": "assistant", "content": raw_text})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Jawabanmu tidak valid terhadap JSON schema yang diminta. "
                            f"Detail error:\n{last_error[:1500]}\n\n"
                            "Kirim ulang SATU objek JSON yang benar sesuai schema, "
                            "tanpa teks lain. Perhatikan nilai enum yang diizinkan "
                            "pada tiap field."
                        ),
                    }
                )

        raise LLMClientError(last_error)

    def assess_rab(self, system_instruction: str, prompt: str) -> LLMValidationResult:
        return self.generate_structured(system_instruction, prompt, LLMValidationResult)

    def extract_search_keywords(self, items: list[tuple[str, str]]) -> dict[str, str]:
        """items = daftar (id, nama). Kembalikan {id: keyword}. Melempar
        LLMClientError bila gagal (pemanggil menyediakan fallback heuristik)."""
        lines = "\n".join(f"- id={item_id} | nama=\"{name}\"" for item_id, name in items)
        prompt = f"Buat keyword pencarian untuk item berikut:\n{lines}"
        result = self.generate_structured(_KEYWORD_SYSTEM_INSTRUCTION, prompt, LLMKeywordResult)
        return {kw.id: kw.keyword.strip() for kw in result.keywords if kw.keyword.strip()}
