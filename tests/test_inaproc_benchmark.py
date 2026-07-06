import httpx
import pytest

from app.config import Settings
from app.models import (
    BenchmarkResult,
    EvaluateRABItem,
    EvaluateRABRequest,
    Fairness,
    Confidence,
)
from app.services.benchmark import InaprocBenchmark, StubBenchmark, build_benchmark
from app.services.keywords import heuristic_keyword
from app.services.llm_client import LLMClientError, LLMItemAssessment, LLMValidationResult
from app.services.validator import RABValidator


def make_settings(**overrides) -> Settings:
    values = {"llm_api_key": "dummy", "internal_token": "x"}
    values.update(overrides)
    return Settings(_env_file=None, **values)


# --- Keyword heuristik --------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Semen Portland 40kg Tiga Roda", "semen portland"),
        ("Pasir cor 1 m3", "pasir cor"),
        ("Kursi roda standar", "kursi roda"),
        ("Cat tembok 5 liter", "cat tembok"),
        ("40kg", "kg"),  # semua angka tersapu → fallback ke sisa huruf nama asli
    ],
)
def test_heuristic_keyword(name, expected):
    assert heuristic_keyword(name) == expected


# --- InaprocBenchmark: agregasi harga ----------------------------------------


def _inaproc_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://inaproc.test")


def test_inaproc_lookup_computes_median_range():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/produk"
        assert request.url.params["keyword"] == "semen"
        return httpx.Response(
            200,
            json={
                "keyword": "semen",
                "items": [
                    {"nama": "Semen A", "harga": 60000},
                    {"nama": "Semen B", "harga": 65000},
                    {"nama": "Semen C", "harga": 70000},
                    {"nama": "Semen rusak", "harga": None},  # diabaikan
                ],
            },
        )

    bench = InaprocBenchmark(make_settings(inaproc_min_samples=3), client=_inaproc_client(handler))
    result = bench.lookup("semen")

    assert isinstance(result, BenchmarkResult)
    assert result.median_price == 65000
    assert result.min_price == 60000
    assert result.max_price == 70000
    assert result.sample_count == 3
    assert result.keyword == "semen"


def test_inaproc_lookup_returns_none_when_too_few_samples():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [{"nama": "X", "harga": 1000}]})

    bench = InaprocBenchmark(make_settings(inaproc_min_samples=3), client=_inaproc_client(handler))
    assert bench.lookup("langka") is None


def test_inaproc_lookup_handles_http_error_gracefully():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"error": "Cloudflare"})

    bench = InaprocBenchmark(make_settings(), client=_inaproc_client(handler))
    assert bench.lookup("semen") is None  # tidak melempar, hanya None


def test_inaproc_empty_keyword_returns_none():
    bench = InaprocBenchmark(make_settings(), client=_inaproc_client(lambda r: httpx.Response(200, json={})))
    assert bench.lookup("  ") is None


def test_build_benchmark_factory():
    assert isinstance(build_benchmark(make_settings(inaproc_enabled=False)), StubBenchmark)
    assert isinstance(build_benchmark(make_settings(inaproc_enabled=True)), InaprocBenchmark)


# --- Validator: keyword LLM-gagal → fallback heuristik + benchmark dipakai ----


class FakeLLM:
    def __init__(self, result: LLMValidationResult, keyword_error: bool = False) -> None:
        self._result = result
        self._keyword_error = keyword_error
        self.assess_prompt: str | None = None

    def extract_search_keywords(self, items):
        if self._keyword_error:
            raise LLMClientError("keyword LLM down")
        return {item_id: "semen" for item_id, _ in items}

    def assess_rab(self, system_instruction, prompt):
        self.assess_prompt = prompt
        return self._result


class SpyBenchmark(StubBenchmark):
    def __init__(self, result: BenchmarkResult | None) -> None:
        self._result = result
        self.keywords: list[str] = []

    def lookup(self, keyword: str) -> BenchmarkResult | None:
        self.keywords.append(keyword)
        return self._result


def _llm_result(score=90):
    return LLMValidationResult(
        overall_score=score,
        summary="ok",
        item_assessments=[
            LLMItemAssessment(id="item-1", fairness=Fairness.WAJAR, reason="sesuai benchmark", confidence=Confidence.SEDANG)
        ],
        flags=[],
    )


def _eval_payload():
    return EvaluateRABRequest(
        items=[EvaluateRABItem(name="Semen Portland 40kg", qty=50, unitPrice=65000)],
        total=3250000,
        targetAmount=4000000,
    )


def test_keyword_llm_failure_falls_back_to_heuristic():
    bench = SpyBenchmark(result=None)
    validator = RABValidator(llm_client=FakeLLM(_llm_result(), keyword_error=True), benchmark=bench)

    validator.evaluate(_eval_payload())

    # LLM gagal → heuristic_keyword('Semen Portland 40kg') = 'semen portland'
    assert bench.keywords == ["semen portland"]


def test_evaluate_maps_to_backend_contract_with_benchmark():
    bench_result = BenchmarkResult(
        keyword="semen", median_price=64000, min_price=60000, max_price=70000,
        sample_count=5, source="INAPROC", sample_names=["Semen A"],
    )
    bench = SpyBenchmark(result=bench_result)
    validator = RABValidator(llm_client=FakeLLM(_llm_result(score=88)), benchmark=bench)

    response = validator.evaluate(_eval_payload())

    assert response.score == 88
    assert response.reasonable is True  # >= 60
    assert response.benchmark_source == "INAPROC"
    assert response.total == 3250000
    assert response.item_assessments[0].benchmark_price == 64000
    # benchmark median masuk ke prompt agar LLM memakainya sebagai acuan
    assert "median Rp64.000" in validator._llm_client.assess_prompt


def test_evaluate_low_score_not_reasonable():
    bench = SpyBenchmark(result=None)
    validator = RABValidator(llm_client=FakeLLM(_llm_result(score=40)), benchmark=bench)

    response = validator.evaluate(_eval_payload())

    assert response.score == 40
    assert response.reasonable is False
    assert response.benchmark_source == "LLM_KNOWLEDGE"
