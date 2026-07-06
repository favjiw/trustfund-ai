import logging

from app.models import (
    BenchmarkResult,
    CampaignType,
    EvaluateRABRequest,
    EvaluateRABResponse,
    ItemAssessment,
    RABItemRequest,
    ValidateRABRequest,
    ValidateRABResponse,
    Verdict,
)
from app.prompts.rab_prompt import SYSTEM_INSTRUCTION, build_prompt
from app.services.benchmark import PriceBenchmark
from app.services.keywords import heuristic_keyword
from app.services.llm_client import LLMClient, LLMClientError

logger = logging.getLogger(__name__)


class RABValidationError(Exception):
    """Gagal memvalidasi RAB (mis. LLM gagal atau keluar dari schema yang diharapkan)."""


def _score_to_verdict(score: int) -> Verdict:
    if score >= 80:
        return Verdict.WAJAR
    if score >= 50:
        return Verdict.PERLU_REVIEW
    return Verdict.MENCURIGAKAN


class RABValidator:
    """Orkestrasi: keyword -> benchmark INAPROC -> prompt -> LLM -> susun response."""

    def __init__(self, llm_client: LLMClient, benchmark: PriceBenchmark) -> None:
        self._llm_client = llm_client
        self._benchmark = benchmark

    # -- Keyword + benchmark ------------------------------------------------------

    def _resolve_keywords(self, items: list[RABItemRequest]) -> dict[str, str]:
        """Turunkan keyword pencarian INAPROC per item. Coba LLM sekali (batched);
        jatuh ke heuristik untuk item yang tidak terisi / bila LLM gagal."""
        keywords: dict[str, str] = {}
        try:
            keywords = self._llm_client.extract_search_keywords([(it.id, it.name) for it in items])
        except LLMClientError as exc:
            logger.warning("Ekstraksi keyword via LLM gagal, memakai heuristik: %s", exc)

        for item in items:
            if not keywords.get(item.id):
                keywords[item.id] = heuristic_keyword(item.name)
        return keywords

    def _lookup_benchmarks(self, items: list[RABItemRequest]) -> dict[str, BenchmarkResult | None]:
        # Tanpa sumber benchmark, lewati ekstraksi keyword LLM (hemat 1 panggilan per evaluasi).
        if not self._benchmark.enabled:
            return {item.id: None for item in items}

        keywords = self._resolve_keywords(items)
        cache: dict[str, BenchmarkResult | None] = {}
        benchmarks: dict[str, BenchmarkResult | None] = {}
        for item in items:
            kw = keywords[item.id]
            if kw not in cache:
                cache[kw] = self._benchmark.lookup(kw)
            benchmarks[item.id] = cache[kw]
        return benchmarks

    # -- Validasi RAB (kontrak kaya /api/v1/validate-rab) -------------------------

    def validate(self, payload: ValidateRABRequest) -> ValidateRABResponse:
        benchmarks = self._lookup_benchmarks(payload.items)
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

            bench = benchmarks.get(item.id)
            item_assessments.append(
                ItemAssessment(
                    id=item.id,
                    name=item.name,
                    unit_price=item.unit_price,
                    benchmark_price=bench.median_price if bench else None,
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

    # -- Kontrak backend Node.js (POST /evaluate-rab) ----------------------------

    def evaluate(self, payload: EvaluateRABRequest) -> EvaluateRABResponse:
        """Adapter untuk backend NexTrust: item {name,qty,unitPrice} + total +
        targetAmount → {score, reasonable, notes}. Internal memakai pipeline
        validate() yang sama (keyword → benchmark INAPROC → LLM)."""
        rab_items = [
            RABItemRequest(
                id=f"item-{idx}",
                name=it.name,
                quantity=it.qty,
                unit="",
                unit_price=it.unit_price,
                subtotal=round(it.qty * it.unit_price, 2),
            )
            for idx, it in enumerate(payload.items, start=1)
        ]

        rich = self.validate(
            ValidateRABRequest(
                campaign_id="rab-check",
                campaign_type=payload.campaign_type or CampaignType.PENGADAAN_BARANG,
                campaign_title=payload.campaign_title or "",
                campaign_description=payload.campaign_description or "",
                location=payload.location or "",
                items=rab_items,
            )
        )

        benchmarked = any(a.benchmark_price is not None for a in rich.item_assessments)
        benchmark_source = "INAPROC" if benchmarked else "LLM_KNOWLEDGE"

        # reasonable: konsisten dengan ambang backend (mock lama memakai >= 60).
        reasonable = rich.overall_score >= 60

        notes = rich.summary
        if rich.flags:
            notes = f"{notes} Catatan: {'; '.join(rich.flags)}."

        total = payload.total if payload.total is not None else rich.total_declared

        return EvaluateRABResponse(
            score=rich.overall_score,
            reasonable=reasonable,
            notes=notes,
            verdict=rich.verdict,
            total=total,
            benchmark_source=benchmark_source,
            item_assessments=rich.item_assessments,
            flags=rich.flags,
        )
