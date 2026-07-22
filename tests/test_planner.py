import pytest

from app.config import Settings
from app.models import CampaignType, EvidenceType, PlanMilestonesRequest, RABItemRequest
from app.services.llm_client import LLMClientError
from app.services.planner import (
    LLMPlannedMilestone,
    LLMPlanResult,
    MilestonePlanner,
    PlannerError,
)


class FakeLLMClient:
    """Mengembalikan hasil berurutan per panggilan (untuk menguji retry)."""

    def __init__(self, results: list[LLMPlanResult | Exception]) -> None:
        self._results = list(results)
        self.prompts: list[str] = []

    def generate_structured(self, system_instruction, prompt, schema, image=None, temperature=0.2):
        self.prompts.append(prompt)
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def make_settings(**overrides) -> Settings:
    values = {"llm_api_key": "dummy", "internal_token": "x"}
    values.update(overrides)
    return Settings(_env_file=None, **values)


def make_payload() -> PlanMilestonesRequest:
    return PlanMilestonesRequest(
        campaign_id="camp-1",
        campaign_type=CampaignType.PEMBANGUNAN,
        campaign_title="Renovasi MCK Panti",
        campaign_description="Renovasi 2 unit MCK panti asuhan",
        location="Bandung",
        duration_days=90,
        items=[
            RABItemRequest(id="p1", name="Semen 50kg", quantity=100, unit="sak", unit_price=70000, subtotal=7000000),
            RABItemRequest(id="p2", name="Pasir", quantity=10, unit="m3", unit_price=300000, subtotal=3000000),
        ],
    )


EVIDENCE = [EvidenceType.NOTA, EvidenceType.FOTO_GEOTAG]


def make_milestone(pct: float, item_ids: list[str]) -> LLMPlannedMilestone:
    return LLMPlannedMilestone(
        title="Tahap",
        percentage=pct,
        definition_of_done="Selesai sesuai rencana",
        evidence_types=EVIDENCE,
        item_ids=item_ids,
        reason="Alokasi retensi progresif",
    )


def valid_plan() -> LLMPlanResult:
    return LLMPlanResult(
        milestones=[
            make_milestone(15, ["p1"]),
            make_milestone(25, ["p2"]),
            make_milestone(25, []),
            make_milestone(35, []),
        ],
        summary="Draf 4 tahap dengan retensi akhir terbesar.",
    )


def test_plan_valid_langsung_lolos():
    llm = FakeLLMClient([valid_plan()])
    planner = MilestonePlanner(llm_client=llm, settings=make_settings())

    result = planner.plan(make_payload())

    assert result.structure_check.valid
    assert len(result.milestones) == 4
    assert result.total_amount == 10_000_000
    # Nominal dihitung kode; jumlahnya tepat sama dengan total RAB.
    assert sum(m.amount for m in result.milestones) == pytest.approx(10_000_000)
    assert result.milestones[-1].percentage == 35
    assert len(llm.prompts) == 1


def test_plan_melanggar_lalu_retry_dengan_feedback():
    bad = LLMPlanResult(
        milestones=[
            make_milestone(50, ["p1"]),  # DP 50% + porsi > 40%
            make_milestone(50, ["p2"]),
        ],
        summary="Draf buruk",
    )
    llm = FakeLLMClient([bad, valid_plan()])
    planner = MilestonePlanner(llm_client=llm, settings=make_settings())

    result = planner.plan(make_payload())

    assert result.structure_check.valid
    assert len(llm.prompts) == 2
    assert "PERBAIKAN" in llm.prompts[1]


def test_plan_tetap_melanggar_gagal():
    bad = LLMPlanResult(milestones=[make_milestone(100, ["p1", "p2"])], summary="Satu tahap")
    llm = FakeLLMClient([bad, bad])
    planner = MilestonePlanner(llm_client=llm, settings=make_settings())

    with pytest.raises(PlannerError):
        planner.plan(make_payload())


def test_item_mapping_hilang_atau_dobel_dianggap_pelanggaran():
    bad_mapping = LLMPlanResult(
        milestones=[
            make_milestone(15, ["p1", "p1"]),  # dobel, p2 hilang
            make_milestone(40, ["p9"]),  # id tak dikenal
            make_milestone(45, []),
        ],
        summary="Mapping kacau",
    )
    llm = FakeLLMClient([bad_mapping, bad_mapping])
    planner = MilestonePlanner(
        llm_client=llm, settings=make_settings(milestone_max_pct=50.0)
    )

    with pytest.raises(PlannerError) as exc:
        planner.plan(make_payload())
    assert "p2" in str(exc.value)


def test_llm_gagal_jadi_planner_error():
    llm = FakeLLMClient([LLMClientError("timeout")])
    planner = MilestonePlanner(llm_client=llm, settings=make_settings())

    with pytest.raises(PlannerError):
        planner.plan(make_payload())
