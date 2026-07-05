from enum import Enum

from pydantic import BaseModel, Field


class CampaignType(str, Enum):
    PEMBANGUNAN = "PEMBANGUNAN"
    PENGADAAN_BARANG = "PENGADAAN_BARANG"
    ALAT_BANTU = "ALAT_BANTU"
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
