import pytest

from app.models import (
    CampaignType,
    Confidence,
    Fairness,
    RABItemRequest,
    ValidateRABRequest,
    Verdict,
)
from app.services.benchmark import StubBenchmark
from app.services.llm_client import LLMClientError, LLMItemAssessment, LLMValidationResult
from app.services.validator import RABValidationError, RABValidator


class FakeLLMClient:
    """Menggantikan GeminiClient di test agar tidak memanggil API asli."""

    def __init__(self, result: LLMValidationResult | None = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error

    def assess_rab(self, system_instruction: str, prompt: str) -> LLMValidationResult:
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


def make_payload(items: list[RABItemRequest] | None = None) -> ValidateRABRequest:
    return ValidateRABRequest(
        campaign_id="camp-1",
        campaign_type=CampaignType.PEMBANGUNAN,
        campaign_title="Bangun Sumur Bersih",
        campaign_description="Pembangunan sumur bor untuk desa terpencil",
        location="Bandung, Jawa Barat",
        items=items
        or [
            RABItemRequest(id="item-1", name="Semen", quantity=50, unit="sak", unit_price=65000, subtotal=3250000),
            RABItemRequest(id="item-2", name="Pasir", quantity=10, unit="m3", unit_price=300000, subtotal=3000000),
        ],
    )


def make_llm_result(overall_score: int, flags: list[str] | None = None) -> LLMValidationResult:
    return LLMValidationResult(
        overall_score=overall_score,
        summary="Ringkasan penilaian RAB.",
        item_assessments=[
            LLMItemAssessment(id="item-1", fairness=Fairness.WAJAR, reason="Sesuai harga pasar", confidence=Confidence.RENDAH),
            LLMItemAssessment(id="item-2", fairness=Fairness.WAJAR, reason="Sesuai harga pasar", confidence=Confidence.RENDAH),
        ],
        flags=flags or [],
    )


def test_validate_maps_high_score_to_wajar_and_merges_item_fields():
    llm_result = make_llm_result(overall_score=90)
    validator = RABValidator(llm_client=FakeLLMClient(result=llm_result), benchmark=StubBenchmark())

    response = validator.validate(make_payload())

    assert response.overall_score == 90
    assert response.verdict == Verdict.WAJAR
    assert response.total_declared == 6250000
    assert len(response.item_assessments) == 2
    assert response.item_assessments[0].unit_price == 65000
    assert response.item_assessments[0].benchmark_price is None
    assert response.item_assessments[0].fairness == Fairness.WAJAR


@pytest.mark.parametrize(
    "score,expected_verdict",
    [
        (100, Verdict.WAJAR),
        (80, Verdict.WAJAR),
        (79, Verdict.PERLU_REVIEW),
        (50, Verdict.PERLU_REVIEW),
        (49, Verdict.MENCURIGAKAN),
        (0, Verdict.MENCURIGAKAN),
    ],
)
def test_score_to_verdict_thresholds(score: int, expected_verdict: Verdict):
    validator = RABValidator(llm_client=FakeLLMClient(result=make_llm_result(score)), benchmark=StubBenchmark())

    response = validator.validate(make_payload())

    assert response.verdict == expected_verdict


def test_validate_raises_when_llm_omits_an_item_assessment():
    llm_result = LLMValidationResult(
        overall_score=90,
        summary="Ringkasan",
        item_assessments=[
            LLMItemAssessment(id="item-1", fairness=Fairness.WAJAR, reason="r", confidence=Confidence.RENDAH),
        ],
        flags=[],
    )
    validator = RABValidator(llm_client=FakeLLMClient(result=llm_result), benchmark=StubBenchmark())

    with pytest.raises(RABValidationError):
        validator.validate(make_payload())


def test_validate_wraps_llm_client_error():
    validator = RABValidator(
        llm_client=FakeLLMClient(error=LLMClientError("Gemini timeout")),
        benchmark=StubBenchmark(),
    )

    with pytest.raises(RABValidationError):
        validator.validate(make_payload())
