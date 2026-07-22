from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CampaignType(str, Enum):
    # Selaras dengan enum CampaignCategory di skema Prisma backend NexTrust.
    PEMBANGUNAN = "PEMBANGUNAN"
    PENGADAAN_BARANG = "PENGADAAN_BARANG"
    ALAT_KESEHATAN = "ALAT_KESEHATAN"
    REKONSTRUKSI = "REKONSTRUKSI"


class Verdict(str, Enum):
    WAJAR = "WAJAR"
    PERLU_REVIEW = "PERLU_REVIEW"
    MENCURIGAKAN = "MENCURIGAKAN"


class Fairness(str, Enum):
    WAJAR = "WAJAR"
    AGAK_TINGGI = "AGAK_TINGGI"
    TIDAK_WAJAR = "TIDAK_WAJAR"
    TIDAK_RELEVAN = "TIDAK_RELEVAN"


class Confidence(str, Enum):
    TINGGI = "TINGGI"
    SEDANG = "SEDANG"
    RENDAH = "RENDAH"


class RABItemRequest(BaseModel):
    id: str
    name: str
    quantity: float
    unit: str
    unit_price: float = Field(ge=0)
    subtotal: float = Field(ge=0)


class ValidateRABRequest(BaseModel):
    campaign_id: str
    campaign_type: CampaignType
    campaign_title: str
    campaign_description: str
    location: str
    items: list[RABItemRequest] = Field(min_length=1)


class BenchmarkResult(BaseModel):
    """Hasil lookup harga pembanding dari sumber e-katalog (mis. INAPROC)."""

    keyword: str
    median_price: float
    min_price: float
    max_price: float
    sample_count: int
    source: str
    sample_names: list[str] = Field(default_factory=list)


class ItemAssessment(BaseModel):
    id: str
    name: str
    unit_price: float
    benchmark_price: float | None = None
    fairness: Fairness
    reason: str
    confidence: Confidence


class ValidateRABResponse(BaseModel):
    campaign_id: str
    overall_score: int = Field(ge=0, le=100)
    verdict: Verdict
    summary: str
    total_declared: float
    item_assessments: list[ItemAssessment]
    flags: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Kontrak yang dipanggil backend Node.js: POST /evaluate-rab
# (lihat rabService.evaluateWithAI di NexTrust-Backend). Item memakai
# {name, qty, unitPrice}; respons memakai {score, reasonable, notes}.
# ---------------------------------------------------------------------------


class EvaluateRABItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    qty: float = Field(gt=0)
    unit_price: float = Field(ge=0, alias="unitPrice")


class EvaluateRABRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    items: list[EvaluateRABItem] = Field(min_length=1)
    total: float | None = None
    target_amount: float | None = Field(default=None, alias="targetAmount")

    # Opsional & forward-compatible: bila backend nanti mengirim konteks kampanye,
    # penilaian relevansi item jadi lebih akurat. Tanpa ini pun tetap jalan.
    campaign_type: CampaignType | None = Field(default=None, alias="campaignType")
    campaign_title: str | None = Field(default=None, alias="campaignTitle")
    campaign_description: str | None = Field(default=None, alias="campaignDescription")
    location: str | None = None


class EvaluateRABResponse(BaseModel):
    # Field yang dibaca backend & disimpan ke tabel RabCheck:
    score: int = Field(ge=0, le=100)
    reasonable: bool
    notes: str

    # Field tambahan (diabaikan backend saat ini, berguna untuk debug/audit):
    verdict: Verdict
    total: float
    benchmark_source: str
    item_assessments: list[ItemAssessment] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# OCR Assist
# ---------------------------------------------------------------------------


class SuggestedItem(BaseModel):
    name: str
    quantity: float | None = None
    unit_price: float | None = None
    subtotal: float | None = None
    confidence: Confidence


class OcrAssistResponse(BaseModel):
    source: Literal["paddleocr", "vision_fallback"]
    raw_text: str
    suggested_items: list[SuggestedItem] = Field(default_factory=list)
    detected_total: float | None = None
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Validator Bukti Milestone
# ---------------------------------------------------------------------------


class MilestoneVerdict(str, Enum):
    LOLOS = "LOLOS"
    PERLU_REVIEW = "PERLU_REVIEW"
    DITOLAK = "DITOLAK"


class Suspicion(str, Enum):
    RENDAH = "RENDAH"
    SEDANG = "SEDANG"
    TINGGI = "TINGGI"


class ConfirmedItem(BaseModel):
    name: str
    quantity: float
    unit_price: float = Field(ge=0)
    subtotal: float = Field(ge=0)


class ElaSignal(BaseModel):
    photo_ref: str
    suspicion: Suspicion


class EvidenceMeta(BaseModel):
    photo_count: int = Field(ge=0)
    location_verified: bool
    distance_from_project_m: float | None = None
    captured_at: str | None = None
    ela_signals: list[ElaSignal] = Field(default_factory=list)


class ValidateMilestoneRequest(BaseModel):
    campaign_id: str
    milestone_id: str
    campaign_type: CampaignType
    milestone_target_amount: float = Field(gt=0)
    planned_items: list[RABItemRequest] = Field(min_length=1)
    confirmed_items: list[ConfirmedItem] = Field(min_length=1)
    evidence_meta: EvidenceMeta


class AmountCheck(BaseModel):
    target: float
    declared: float
    diff_pct: float
    status: Literal["OK", "REVIEW", "SUSPICIOUS"]


class MatchingResult(BaseModel):
    planned_name: str
    matched: Literal["YA", "SEBAGIAN", "TIDAK"]
    note: str


class ForensicSummary(BaseModel):
    location: Literal["OK", "FLAG"]
    image: Literal["OK", "FLAG", "REVIEW"]
    note: str


class ValidateMilestoneResponse(BaseModel):
    campaign_id: str
    milestone_id: str
    overall_score: int = Field(ge=0, le=100)
    verdict: MilestoneVerdict
    summary: str
    amount_check: AmountCheck
    matching: list[MatchingResult]
    forensic_summary: ForensicSummary
    flags: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Forensic ELA endpoint
# ---------------------------------------------------------------------------


class ElaResponse(BaseModel):
    photo_ref: str | None = None
    suspicion: Suspicion
    metrics: dict[str, float]


# ---------------------------------------------------------------------------
# AI Planner & pagar struktur milestone
# ---------------------------------------------------------------------------


class EvidenceType(str, Enum):
    NOTA = "NOTA"
    FOTO_GEOTAG = "FOTO_GEOTAG"
    SERAH_TERIMA = "SERAH_TERIMA"
    LAPORAN = "LAPORAN"


class StructureViolation(BaseModel):
    code: str
    message: str


class StructureCheckResult(BaseModel):
    valid: bool
    violations: list[StructureViolation] = Field(default_factory=list)
    warnings: list[StructureViolation] = Field(default_factory=list)


class StructureMilestoneInput(BaseModel):
    order: int = Field(ge=1)
    title: str = ""
    percentage: float = Field(gt=0, le=100)
    # Kosong = backend hanya mengecek struktur porsi, tanpa cek paket bukti.
    evidence_types: list[EvidenceType] = Field(default_factory=list)


class ValidateStructureRequest(BaseModel):
    campaign_type: CampaignType
    milestones: list[StructureMilestoneInput] = Field(min_length=1)


class PlanMilestonesRequest(BaseModel):
    campaign_id: str
    campaign_type: CampaignType
    campaign_title: str
    campaign_description: str
    location: str
    duration_days: int | None = Field(default=None, gt=0)
    items: list[RABItemRequest] = Field(min_length=1)


class PlannedMilestone(BaseModel):
    order: int
    title: str
    percentage: float
    amount: float
    definition_of_done: str
    evidence_types: list[EvidenceType]
    item_ids: list[str]
    reason: str


class PlanMilestonesResponse(BaseModel):
    campaign_id: str
    total_amount: float
    milestones: list[PlannedMilestone]
    structure_check: StructureCheckResult
    summary: str
