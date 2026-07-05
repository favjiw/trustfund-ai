from app.models import ItemAssessment, ValidateRABRequest, ValidateRABResponse, Verdict
from app.prompts.rab_prompt import SYSTEM_INSTRUCTION, build_prompt
from app.services.benchmark import PriceBenchmark
from app.services.llm_client import GeminiClient, LLMClientError


class RABValidationError(Exception):
    """Gagal memvalidasi RAB (mis. LLM gagal atau keluar dari schema yang diharapkan)."""


def _score_to_verdict(score: int) -> Verdict:
    if score >= 80:
        return Verdict.WAJAR
    if score >= 50:
        return Verdict.PERLU_REVIEW
    return Verdict.MENCURIGAKAN


class RABValidator:
    """Orkestrasi: benchmark lookup -> build prompt -> panggil LLM -> susun response."""

    def __init__(self, llm_client: GeminiClient, benchmark: PriceBenchmark) -> None:
        self._llm_client = llm_client
        self._benchmark = benchmark

    def validate(self, payload: ValidateRABRequest) -> ValidateRABResponse:
        benchmarks = {
            item.id: self._benchmark.get_benchmark_price(item, payload.location)
            for item in payload.items
        }

        prompt = build_prompt(payload, benchmarks)

        try:
            llm_result = self._llm_client.assess_rab(SYSTEM_INSTRUCTION, prompt)
        except LLMClientError as exc:
            raise RABValidationError(str(exc)) from exc

        llm_by_id = {assessment.id: assessment for assessment in llm_result.item_assessments}

        item_assessments: list[ItemAssessment] = []
        for item in payload.items:
            llm_item = llm_by_id.get(item.id)
            if llm_item is None:
                raise RABValidationError(f"LLM tidak memberi penilaian untuk item id={item.id}")

            item_assessments.append(
                ItemAssessment(
                    id=item.id,
                    name=item.name,
                    unit_price=item.unit_price,
                    benchmark_price=benchmarks.get(item.id),
                    fairness=llm_item.fairness,
                    reason=llm_item.reason,
                    confidence=llm_item.confidence,
                )
            )

        overall_score = llm_result.overall_score
        total_declared = sum(item.subtotal for item in payload.items)

        return ValidateRABResponse(
            campaign_id=payload.campaign_id,
            overall_score=overall_score,
            verdict=_score_to_verdict(overall_score),
            summary=llm_result.summary,
            total_declared=total_declared,
            item_assessments=item_assessments,
            flags=llm_result.flags,
        )
