import logging

from pydantic import BaseModel, Field

from app.config import Settings
from app.models import (
    EvidenceType,
    PlanMilestonesRequest,
    PlanMilestonesResponse,
    PlannedMilestone,
    StructureMilestoneInput,
)
from app.prompts.planner_prompt import PLANNER_SYSTEM_INSTRUCTION, build_planner_prompt
from app.services.llm_client import LLMClient, LLMClientError
from app.services.structure_guard import check_structure

logger = logging.getLogger(__name__)


class PlannerError(Exception):
    """Gagal menyusun draf milestone (LLM gagal atau draf tetap melanggar pagar)."""


class LLMPlannedMilestone(BaseModel):
    title: str
    percentage: float = Field(gt=0, le=100)
    definition_of_done: str
    evidence_types: list[EvidenceType] = Field(min_length=1)
    item_ids: list[str] = Field(default_factory=list)
    reason: str


class LLMPlanResult(BaseModel):
    milestones: list[LLMPlannedMilestone] = Field(min_length=1)
    summary: str


class MilestonePlanner:
    """AI Planner (§3.4 konsep): LLM menyusun draf struktur milestone, KODE yang
    menegakkan pagar (structure_guard). Draf yang melanggar dikembalikan ke LLM
    dengan feedback pelanggaran; bila tetap melanggar setelah retry, gagal —
    tidak pernah meloloskan struktur di luar pagar."""

    def __init__(self, llm_client: LLMClient, settings: Settings) -> None:
        self._llm_client = llm_client
        self._settings = settings

    # -- Cek pemetaan item RAB -> milestone (aturan kode, bukan LLM) --------------

    def _check_item_mapping(
        self, payload: PlanMilestonesRequest, milestones: list[LLMPlannedMilestone]
    ) -> list[str]:
        problems: list[str] = []
        valid_ids = {item.id for item in payload.items}

        seen: dict[str, int] = {}
        for idx, m in enumerate(milestones, start=1):
            for item_id in m.item_ids:
                if item_id not in valid_ids:
                    problems.append(f"Milestone #{idx} memakai item_id tidak dikenal: {item_id}.")
                seen[item_id] = seen.get(item_id, 0) + 1

        missing = valid_ids - seen.keys()
        if missing:
            problems.append(
                f"Item RAB belum masuk milestone mana pun: {', '.join(sorted(missing))}."
            )
        duplicated = [item_id for item_id, count in seen.items() if count > 1 and item_id in valid_ids]
        if duplicated:
            problems.append(
                f"Item RAB masuk lebih dari satu milestone: {', '.join(sorted(duplicated))}."
            )
        return problems

    # -- Orkestrasi ----------------------------------------------------------------

    def plan(self, payload: PlanMilestonesRequest) -> PlanMilestonesResponse:
        settings = self._settings
        total_amount = sum(item.subtotal for item in payload.items)
        if total_amount <= 0:
            raise PlannerError("Total RAB harus lebih dari 0 untuk menyusun milestone.")

        feedback: list[str] | None = None
        llm_result: LLMPlanResult | None = None
        guard_result = None

        for attempt in range(1, settings.planner_max_attempts + 1):
            prompt = build_planner_prompt(payload, total_amount, settings, feedback)
            try:
                llm_result = self._llm_client.generate_structured(
                    PLANNER_SYSTEM_INSTRUCTION, prompt, LLMPlanResult, temperature=0.3
                )
            except LLMClientError as exc:
                raise PlannerError(str(exc)) from exc

            structure = [
                StructureMilestoneInput(
                    order=idx,
                    title=m.title,
                    percentage=m.percentage,
                    evidence_types=m.evidence_types,
                )
                for idx, m in enumerate(llm_result.milestones, start=1)
            ]
            guard_result = check_structure(payload.campaign_type, structure, settings)
            problems = [v.message for v in guard_result.violations]
            problems += self._check_item_mapping(payload, llm_result.milestones)

            if not problems:
                break

            logger.warning(
                "Draf planner melanggar pagar (attempt %d/%d): %s",
                attempt,
                settings.planner_max_attempts,
                "; ".join(problems),
            )
            feedback = problems
        else:
            raise PlannerError(
                "Draf milestone tetap melanggar pagar setelah "
                f"{settings.planner_max_attempts} percobaan: {'; '.join(feedback or [])}"
            )

        assert llm_result is not None and guard_result is not None

        # Nominal dihitung KODE dari persen; milestone final menyerap sisa pembulatan
        # supaya jumlah nominal tepat sama dengan total RAB.
        milestones: list[PlannedMilestone] = []
        allocated = 0.0
        for idx, m in enumerate(llm_result.milestones, start=1):
            is_last = idx == len(llm_result.milestones)
            amount = round(total_amount - allocated, 2) if is_last else round(total_amount * m.percentage / 100.0, 2)
            allocated += amount
            milestones.append(
                PlannedMilestone(
                    order=idx,
                    title=m.title,
                    percentage=round(m.percentage, 2),
                    amount=amount,
                    definition_of_done=m.definition_of_done,
                    evidence_types=m.evidence_types,
                    item_ids=m.item_ids,
                    reason=m.reason,
                )
            )

        return PlanMilestonesResponse(
            campaign_id=payload.campaign_id,
            total_amount=total_amount,
            milestones=milestones,
            structure_check=guard_result,
            summary=llm_result.summary,
        )
