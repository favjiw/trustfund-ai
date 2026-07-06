import logging
from typing import Literal

from pydantic import BaseModel, Field

from app.config import Settings
from app.models import (
    AmountCheck,
    ForensicSummary,
    MatchingResult,
    MilestoneVerdict,
    Suspicion,
    ValidateMilestoneRequest,
    ValidateMilestoneResponse,
)
from app.prompts.milestone_prompt import MILESTONE_SYSTEM_INSTRUCTION, build_milestone_prompt
from app.services.llm_client import LLMClient, LLMClientError

logger = logging.getLogger(__name__)


class MilestoneValidationError(Exception):
    """Gagal memvalidasi bukti milestone (mis. LLM gagal)."""


class LLMMatching(BaseModel):
    planned_id: str
    planned_name: str
    matched: Literal["YA", "SEBAGIAN", "TIDAK"]
    note: str


class LLMMilestoneResult(BaseModel):
    fairness_score: int = Field(ge=0, le=100)
    matching: list[LLMMatching]
    summary: str
    flags: list[str] = Field(default_factory=list)


class MilestoneValidator:
    """Validasi 4 lapis: kecocokan isi, kelengkapan & jumlah, kewajaran (LLM),
    dan sinyal forensik. LLM memberi penilaian semantik; KODE yang menggabung
    aturan toleransi + forensik menjadi verdict final."""

    def __init__(self, llm_client: LLMClient, settings: Settings) -> None:
        self._llm_client = llm_client
        self._settings = settings

    # -- Lapis 2a: selisih nominal ------------------------------------------------

    def _check_amount(self, target: float, declared: float) -> AmountCheck:
        diff_pct = (declared - target) / target * 100.0

        review_threshold = self._settings.diff_review_pct
        suspicious_threshold = self._settings.diff_suspicious_pct
        if declared < target:
            # Asimetri: realisasi lebih murah lebih ditoleransi daripada mark-up.
            multiplier = self._settings.underspend_tolerance_multiplier
            review_threshold *= multiplier
            suspicious_threshold *= multiplier

        abs_diff = abs(diff_pct)
        if abs_diff <= review_threshold:
            status: Literal["OK", "REVIEW", "SUSPICIOUS"] = "OK"
        elif abs_diff <= suspicious_threshold:
            status = "REVIEW"
        else:
            status = "SUSPICIOUS"

        return AmountCheck(
            target=target,
            declared=declared,
            diff_pct=round(diff_pct, 2),
            status=status,
        )

    # -- Lapis 4: forensik ---------------------------------------------------------

    def _summarize_forensic(self, payload: ValidateMilestoneRequest) -> tuple[ForensicSummary, Suspicion | None]:
        meta = payload.evidence_meta
        notes: list[str] = []

        location: Literal["OK", "FLAG"] = "OK"
        if not meta.location_verified:
            location = "FLAG"
            notes.append("Lokasi tidak terverifikasi saat upload bukti.")
        elif (
            meta.distance_from_project_m is not None
            and meta.distance_from_project_m > self._settings.geo_radius_meters
        ):
            location = "FLAG"
            notes.append(
                f"Jarak upload {meta.distance_from_project_m:.0f} m dari lokasi proyek "
                f"(radius wajar {self._settings.geo_radius_meters:.0f} m)."
            )

        worst_ela: Suspicion | None = None
        order = {Suspicion.RENDAH: 0, Suspicion.SEDANG: 1, Suspicion.TINGGI: 2}
        for signal in meta.ela_signals:
            if worst_ela is None or order[signal.suspicion] > order[worst_ela]:
                worst_ela = signal.suspicion

        image: Literal["OK", "FLAG", "REVIEW"] = "OK"
        if worst_ela == Suspicion.TINGGI:
            image = "FLAG"
            notes.append("Sinyal ELA TINGGI pada minimal satu foto bukti.")
        elif worst_ela == Suspicion.SEDANG:
            image = "REVIEW"
            notes.append("Sinyal ELA SEDANG pada foto bukti.")

        if not notes:
            notes.append("Tidak ada sinyal forensik yang mencurigakan.")

        return ForensicSummary(location=location, image=image, note=" ".join(notes)), worst_ela

    # -- Orkestrasi -----------------------------------------------------------------

    def validate(self, payload: ValidateMilestoneRequest) -> ValidateMilestoneResponse:
        settings = self._settings
        target = payload.milestone_target_amount
        declared = sum(item.subtotal for item in payload.confirmed_items)

        amount_check = self._check_amount(target, declared)
        forensic_summary, worst_ela = self._summarize_forensic(payload)

        # Lapis 1 & 3: matching semantik + kewajaran via LLM
        prompt = build_milestone_prompt(payload)
        try:
            llm_result = self._llm_client.generate_structured(
                MILESTONE_SYSTEM_INSTRUCTION, prompt, LLMMilestoneResult
            )
        except LLMClientError as exc:
            raise MilestoneValidationError(str(exc)) from exc

        llm_by_id = {m.planned_id: m for m in llm_result.matching}
        planned_by_id = {item.id: item for item in payload.planned_items}

        matching: list[MatchingResult] = []
        missing_subtotals: list[float] = []
        major_missing = False
        for item in payload.planned_items:
            llm_match = llm_by_id.get(item.id)
            if llm_match is None:
                raise MilestoneValidationError(
                    f"LLM tidak memberi hasil matching untuk planned item id={item.id}"
                )
            matching.append(
                MatchingResult(planned_name=item.name, matched=llm_match.matched, note=llm_match.note)
            )
            if llm_match.matched == "TIDAK":
                missing_subtotals.append(item.subtotal)
                if item.subtotal / target * 100.0 > settings.missing_major_item_pct:
                    major_missing = True

        missing_total_pct = sum(missing_subtotals) / target * 100.0 if missing_subtotals else 0.0

        # -- Gabungkan skor & flags (aturan di KODE) --------------------------------
        flags: list[str] = list(llm_result.flags)
        score = llm_result.fairness_score

        if amount_check.status == "REVIEW":
            score -= 10
            flags.append(
                f"Selisih nominal {amount_check.diff_pct:+.1f}% dari target milestone (perlu review)."
            )
        elif amount_check.status == "SUSPICIOUS":
            score -= 25
            flags.append(
                f"Selisih nominal {amount_check.diff_pct:+.1f}% dari target milestone (mencurigakan)."
            )

        if major_missing:
            score -= 15
            flags.append(
                f"Item besar (> {settings.missing_major_item_pct:.0f}% nilai milestone) tidak ada di nota realisasi."
            )
        elif missing_total_pct > settings.missing_minor_total_pct:
            score -= 8
            flags.append(
                f"Total item hilang {missing_total_pct:.1f}% dari nilai milestone."
            )
        # Item kecil hilang (total <= threshold) → boleh, cukup tercatat di matching.

        if worst_ela == Suspicion.TINGGI:
            score -= 20
        elif worst_ela == Suspicion.SEDANG:
            score -= 5

        if not payload.evidence_meta.location_verified:
            score -= 10
            flags.append("Lokasi upload bukti tidak terverifikasi.")
        elif forensic_summary.location == "FLAG":
            score -= 5
            flags.append("Upload bukti di luar radius wajar lokasi proyek.")

        if forensic_summary.image == "FLAG":
            flags.append("Sinyal forensik gambar (ELA) TINGGI; perlu review manual Dinsos.")

        score = max(0, min(100, score))

        # -- Verdict final (di KODE) -------------------------------------------------
        # Kondisi berat: tidak boleh pernah LOLOS otomatis.
        forced_review = (
            worst_ela == Suspicion.TINGGI
            or not payload.evidence_meta.location_verified
            or amount_check.status == "SUSPICIOUS"
            or major_missing
        )

        if score < 50:
            verdict = MilestoneVerdict.DITOLAK
        elif score >= 80 and not flags and not forced_review:
            verdict = MilestoneVerdict.LOLOS
        else:
            verdict = MilestoneVerdict.PERLU_REVIEW

        return ValidateMilestoneResponse(
            campaign_id=payload.campaign_id,
            milestone_id=payload.milestone_id,
            overall_score=score,
            verdict=verdict,
            summary=llm_result.summary,
            amount_check=amount_check,
            matching=matching,
            forensic_summary=forensic_summary,
            flags=flags,
        )
