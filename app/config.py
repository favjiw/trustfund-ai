from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM via API OpenAI-compatible (default: DeepSeek di Sumopod)
    llm_api_key: str = ""
    llm_model: str = "deepseek-v4-pro"
    llm_base_url: str = "https://ai.sumopod.com/v1"
    llm_timeout_seconds: float = 60.0
    llm_max_tokens: int = 4096
    llm_supports_vision: bool = False  # DeepSeek text-only; set true bila model mendukung gambar
    llm_max_retries: int = 2  # retry pada 429/5xx transient
    llm_retry_backoff_seconds: float = 0.5

    internal_token: str = ""
    internal_auth_enabled: bool = True

    cors_origins: str = "*"

    # Benchmark harga e-katalog INAPROC (scraper terpisah, lihat NexTrust-Backend/inaproc-api)
    inaproc_enabled: bool = False
    inaproc_api_url: str = "http://localhost:3000"
    inaproc_timeout_seconds: float = 20.0
    inaproc_per_page: int = 30
    inaproc_min_samples: int = 3  # butuh >= N produk agar benchmark dianggap valid

    # OCR assist
    ocr_vision_fallback: bool = True
    ocr_lang: str = "latin"
    ocr_min_text_len: int = 20

    # Validator bukti milestone: toleransi nominal (persen)
    diff_review_pct: float = 10.0
    diff_suspicious_pct: float = 25.0
    # Realisasi lebih murah lebih ditoleransi: threshold dikali faktor ini
    underspend_tolerance_multiplier: float = 1.5
    # Item nota hilang
    missing_major_item_pct: float = 30.0
    missing_minor_total_pct: float = 15.0
    # Geolokasi
    geo_radius_meters: float = 200.0

    # Forensik ELA (Error Level Analysis)
    ela_jpeg_quality: int = 90
    ela_sedang_threshold: float = 12.0
    ela_tinggi_threshold: float = 20.0

    @property
    def cors_origins_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
