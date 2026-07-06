import logging
import statistics
from abc import ABC, abstractmethod

import httpx

from app.config import Settings
from app.models import BenchmarkResult

logger = logging.getLogger(__name__)


class PriceBenchmark(ABC):
    """Abstraksi sumber harga pembanding (mis. e-katalog INAPROC/LKPP).

    Lookup dipicu KEYWORD (mis. 'semen') — sesuai cara kerja katalog INAPROC
    yang harus dicari per kata kunci. Keyword diturunkan dari nama item RAB
    di lapisan validator (lihat keyword extraction).
    """

    #: True bila ada sumber pembanding nyata (mempengaruhi apakah keyword LLM perlu diambil).
    enabled: bool = False

    @abstractmethod
    def lookup(self, keyword: str) -> BenchmarkResult | None:
        """Kembalikan statistik harga pembanding untuk sebuah keyword, atau
        None bila tidak ada sumber / sampel tidak cukup."""
        raise NotImplementedError


class StubBenchmark(PriceBenchmark):
    """MVP: tidak ada sumber pembanding, selalu kembalikan None."""

    enabled = False

    def lookup(self, keyword: str) -> BenchmarkResult | None:
        return None


class InaprocBenchmark(PriceBenchmark):
    """Ambil harga pembanding dari scraper e-katalog INAPROC.

    Memanggil GET {base_url}/api/produk?keyword=... (service scraper terpisah,
    lihat NexTrust-Backend/inaproc-api). Karena katalog INAPROC harus dipicu
    per keyword, satu lookup = satu keyword. Harga acuan dihitung sebagai
    median `harga` (defaultPrice) dari produk yang ditemukan.
    """

    SOURCE = "katalog.inaproc.id (via inaproc-api)"
    enabled = True

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self._settings = settings
        self._client = client or httpx.Client(
            base_url=settings.inaproc_api_url,
            timeout=settings.inaproc_timeout_seconds,
        )

    def _search_products(self, keyword: str) -> list[dict]:
        response = self._client.get(
            "/api/produk",
            params={"keyword": keyword, "perPage": self._settings.inaproc_per_page},
        )
        response.raise_for_status()
        data = response.json()
        return data.get("items", []) or []

    def lookup(self, keyword: str) -> BenchmarkResult | None:
        keyword = (keyword or "").strip()
        if not keyword:
            return None

        try:
            items = self._search_products(keyword)
        except (httpx.HTTPError, ValueError) as exc:
            # Best-effort: kegagalan benchmark tidak boleh menggagalkan validasi.
            logger.warning("INAPROC lookup gagal untuk keyword '%s': %s", keyword, exc)
            return None

        prices: list[float] = []
        names: list[str] = []
        for it in items:
            harga = it.get("harga")
            if isinstance(harga, (int, float)) and harga > 0:
                prices.append(float(harga))
                if it.get("nama"):
                    names.append(str(it["nama"]))

        if len(prices) < self._settings.inaproc_min_samples:
            logger.info(
                "INAPROC keyword '%s': hanya %d sampel harga (< min %d), benchmark diabaikan.",
                keyword,
                len(prices),
                self._settings.inaproc_min_samples,
            )
            return None

        return BenchmarkResult(
            keyword=keyword,
            median_price=round(statistics.median(prices), 2),
            min_price=min(prices),
            max_price=max(prices),
            sample_count=len(prices),
            source=self.SOURCE,
            sample_names=names[:5],
        )

    def close(self) -> None:
        self._client.close()


def build_benchmark(settings: Settings) -> PriceBenchmark:
    """Pilih implementasi benchmark berdasar konfigurasi."""
    if settings.inaproc_enabled:
        logger.info("Benchmark INAPROC aktif → %s", settings.inaproc_api_url)
        return InaprocBenchmark(settings)
    logger.info("Benchmark INAPROC nonaktif → memakai StubBenchmark (benchmark_price null).")
    return StubBenchmark()
