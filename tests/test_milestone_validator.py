import io

import pytest
from PIL import Image

from app.config import Settings
from app.models import (
    CampaignType,
    ConfirmedItem,
    ElaSignal,
    EvidenceMeta,
    MilestoneVerdict,
    RABItemRequest,
    Suspicion,
    ValidateMilestoneRequest,
)
from app.services.forensic import compute_ela
from app.services.llm_client import LLMClientError
from app.services.milestone_validator import (
    LLMMatching,
    LLMMilestoneResult,
    MilestoneValidationError,
    MilestoneValidator,
)


class FakeLLMClient:
    def __init__(self, result: LLMMilestoneResult | None = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error

    def generate_structured(self, system_instruction, prompt, schema, image=None):
        if self._error is not None:
            raise self._error
        return self._result


def make_settings(**overrides) -> Settings:
    values = {"llm_api_key": "dummy", "internal_token": "x"}
    values.update(overrides)
    return Settings(_env_file=None, **values)


TARGET = 10_000_000.0


def make_payload(
    declared_total: float = TARGET,
    location_verified: bool = True,
    ela_signals: list[ElaSignal] | None = None,
    distance_m: float | None = 50.0,
) -> ValidateMilestoneRequest:
    return ValidateMilestoneRequest(
        campaign_id="camp-1",
        milestone_id="ms-1",
        campaign_type=CampaignType.PEMBANGUNAN,
        milestone_target_amount=TARGET,
        planned_items=[
            RABItemRequest(id="p1", name="Semen 50kg", quantity=100, unit="sak", unit_price=70000, subtotal=7000000),
            RABItemRequest(id="p2", name="Pasir", quantity=10, unit="m3", unit_price=300000, subtotal=3000000),
        ],
        confirmed_items=[
            ConfirmedItem(name="Semen Tiga Roda 50kg", quantity=100, unit_price=declared_total * 0.7 / 100, subtotal=declared_total * 0.7),
            ConfirmedItem(name="Pasir cor", quantity=10, unit_price=declared_total * 0.3 / 10, subtotal=declared_total * 0.3),
        ],
        evidence_meta=EvidenceMeta(
            photo_count=3,
            location_verified=location_verified,
            distance_from_project_m=distance_m,
            captured_at="2026-07-01T10:00:00Z",
            ela_signals=ela_signals or [],
        ),
    )


def make_llm_result(fairness_score: int = 90, matched: str = "YA") -> LLMMilestoneResult:
    return LLMMilestoneResult(
        fairness_score=fairness_score,
        matching=[
            LLMMatching(planned_id="p1", planned_name="Semen 50kg", matched=matched, note="Cocok dengan Semen Tiga Roda 50kg"),
            LLMMatching(planned_id="p2", planned_name="Pasir", matched="YA", note="Cocok dengan Pasir cor"),
        ],
        summary="Realisasi sesuai RAB milestone.",
        flags=[],
    )


def make_validator(llm_result=None, error=None, **settings_overrides) -> MilestoneValidator:
    return MilestoneValidator(
        llm_client=FakeLLMClient(result=llm_result, error=error),
        settings=make_settings(**settings_overrides),
    )


def test_diff_8_pct_within_tolerance_lolos():
    validator = make_validator(llm_result=make_llm_result())

    response = validator.validate(make_payload(declared_total=TARGET * 1.08))

    assert response.amount_check.status == "OK"
    assert response.verdict == MilestoneVerdict.LOLOS


def test_diff_18_pct_forces_review():
    validator = make_validator(llm_result=make_llm_result())

    response = validator.validate(make_payload(declared_total=TARGET * 1.18))

    assert response.amount_check.status == "REVIEW"
    assert response.verdict == MilestoneVerdict.PERLU_REVIEW


def test_diff_30_pct_suspicious_never_lolos():
    validator = make_validator(llm_result=make_llm_result(fairness_score=100))

    response = validator.validate(make_payload(declared_total=TARGET * 1.30))

    assert response.amount_check.status == "SUSPICIOUS"
    assert response.verdict != MilestoneVerdict.LOLOS


def test_underspend_asymmetry_more_tolerated():
    # 12% lebih murah: threshold review 10% * 1.5 = 15% → masih OK
    validator = make_validator(llm_result=make_llm_result())

    response = validator.validate(make_payload(declared_total=TARGET * 0.88))

    assert response.amount_check.status == "OK"
    assert response.verdict == MilestoneVerdict.LOLOS


def test_location_not_verified_forces_minimum_review():
    validator = make_validator(llm_result=make_llm_result(fairness_score=100))

    response = validator.validate(make_payload(location_verified=False))

    assert response.verdict != MilestoneVerdict.LOLOS
    assert response.forensic_summary.location == "FLAG"


def test_ela_tinggi_forces_minimum_review():
    validator = make_validator(llm_result=make_llm_result(fairness_score=100))

    response = validator.validate(
        make_payload(ela_signals=[ElaSignal(photo_ref="foto1.jpg", suspicion=Suspicion.TINGGI)])
    )

    assert response.verdict != MilestoneVerdict.LOLOS
    assert response.forensic_summary.image == "FLAG"


def test_major_missing_item_forces_review():
    # p1 (70% nilai milestone) tidak ada realisasinya
    llm_result = LLMMilestoneResult(
        fairness_score=95,
        matching=[
            LLMMatching(planned_id="p1", planned_name="Semen 50kg", matched="TIDAK", note="Tidak ditemukan di nota"),
            LLMMatching(planned_id="p2", planned_name="Pasir", matched="YA", note="Cocok"),
        ],
        summary="Item utama tidak ditemukan.",
        flags=[],
    )
    validator = make_validator(llm_result=llm_result)

    response = validator.validate(make_payload())

    assert response.verdict != MilestoneVerdict.LOLOS
    assert any("Item besar" in f for f in response.flags)


def test_distance_beyond_radius_flags_but_not_reject():
    validator = make_validator(llm_result=make_llm_result())

    response = validator.validate(make_payload(distance_m=1500.0))

    assert response.forensic_summary.location == "FLAG"
    assert response.verdict == MilestoneVerdict.PERLU_REVIEW  # flag, bukan tolak


def test_llm_failure_raises_structured_error():
    validator = make_validator(error=LLMClientError("Gemini timeout"))

    with pytest.raises(MilestoneValidationError):
        validator.validate(make_payload())


def test_compute_ela_returns_suspicion_and_metrics():
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), color=(120, 130, 140)).save(buffer, format="JPEG", quality=95)

    suspicion, metrics = compute_ela(buffer.getvalue(), make_settings())

    assert suspicion in (Suspicion.RENDAH, Suspicion.SEDANG, Suspicion.TINGGI)
    assert "mean_diff" in metrics and "max_diff" in metrics
