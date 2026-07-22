from app.config import Settings
from app.models import (
    CampaignType,
    EvidenceType,
    StructureCheckResult,
    StructureMilestoneInput,
    StructureViolation,
)

# Rentang jumlah tahap yang wajar per jenis kampanye (pelanggaran ringan = warning;
# batas keras tetap milestone_min_count..milestone_max_count).
_COUNT_RANGE_BY_TYPE: dict[CampaignType, tuple[int, int]] = {
    CampaignType.PENGADAAN_BARANG: (2, 3),
    CampaignType.ALAT_KESEHATAN: (2, 3),
    CampaignType.PEMBANGUNAN: (3, 5),
    CampaignType.REKONSTRUKSI: (3, 5),
}

# Paket bukti wajib per jenis (ditentukan sistem, bukan yayasan).
REQUIRED_EVIDENCE_BY_TYPE: dict[CampaignType, set[EvidenceType]] = {
    CampaignType.PEMBANGUNAN: {EvidenceType.NOTA, EvidenceType.FOTO_GEOTAG},
    CampaignType.PENGADAAN_BARANG: {EvidenceType.NOTA, EvidenceType.FOTO_GEOTAG},
    CampaignType.ALAT_KESEHATAN: {EvidenceType.NOTA, EvidenceType.FOTO_GEOTAG},
    CampaignType.REKONSTRUKSI: {EvidenceType.NOTA, EvidenceType.FOTO_GEOTAG},
}

# Jenis yang wajib bukti serah-terima pada milestone final.
_HANDOVER_REQUIRED_TYPES = {CampaignType.PENGADAAN_BARANG, CampaignType.ALAT_KESEHATAN}


def check_structure(
    campaign_type: CampaignType,
    milestones: list[StructureMilestoneInput],
    settings: Settings,
) -> StructureCheckResult:
    """Pagar struktur milestone — deterministik, tanpa LLM.

    Aturan keras (violation): jumlah tahap 2-6, DP <= dp_max_pct, tidak ada
    milestone > milestone_max_pct, porsi akhir terbesar (retensi), total 100%,
    paket bukti wajib per jenis. Aturan lunak (warning): retensi akhir di bawah
    ambang ideal, jumlah tahap di luar rentang wajar jenis.
    """
    violations: list[StructureViolation] = []
    warnings: list[StructureViolation] = []

    orders = [m.order for m in milestones]
    if len(set(orders)) != len(orders):
        violations.append(
            StructureViolation(code="ORDER_DUPLIKAT", message="Nomor urut milestone tidak boleh duplikat.")
        )
        # Struktur ambigu; cek lain tetap jalan atas urutan apa adanya.

    ordered = sorted(milestones, key=lambda m: m.order)
    n = len(ordered)

    if not settings.milestone_min_count <= n <= settings.milestone_max_count:
        violations.append(
            StructureViolation(
                code="JUMLAH_TAHAP",
                message=(
                    f"Jumlah milestone {n} di luar batas sistem "
                    f"({settings.milestone_min_count}-{settings.milestone_max_count} tahap)."
                ),
            )
        )

    first = ordered[0]
    last = ordered[-1]

    if first.percentage > settings.dp_max_pct:
        violations.append(
            StructureViolation(
                code="DP_MAKS",
                message=(
                    f"DP/milestone pertama {first.percentage:.1f}% melebihi batas "
                    f"{settings.dp_max_pct:.0f}%."
                ),
            )
        )

    for m in ordered:
        if m.percentage > settings.milestone_max_pct:
            violations.append(
                StructureViolation(
                    code="PORSI_MAKS",
                    message=(
                        f"Milestone #{m.order} sebesar {m.percentage:.1f}% melebihi batas "
                        f"{settings.milestone_max_pct:.0f}% per tahap."
                    ),
                )
            )

    if n >= 2:
        largest_other = max(m.percentage for m in ordered[:-1])
        if last.percentage < largest_other:
            violations.append(
                StructureViolation(
                    code="RETENSI_AKHIR",
                    message=(
                        f"Porsi milestone final ({last.percentage:.1f}%) harus yang terbesar "
                        f"(retensi); saat ini ada tahap lain sebesar {largest_other:.1f}%."
                    ),
                )
            )
        elif last.percentage < settings.final_retention_min_pct:
            warnings.append(
                StructureViolation(
                    code="RETENSI_AKHIR_KECIL",
                    message=(
                        f"Retensi akhir {last.percentage:.1f}% di bawah ambang ideal "
                        f"{settings.final_retention_min_pct:.0f}%."
                    ),
                )
            )

    total_pct = sum(m.percentage for m in ordered)
    if abs(total_pct - 100.0) > settings.structure_sum_tolerance_pct:
        violations.append(
            StructureViolation(
                code="TOTAL_PERSEN",
                message=f"Total porsi milestone {total_pct:.1f}% (harus 100%).",
            )
        )

    count_range = _COUNT_RANGE_BY_TYPE.get(campaign_type)
    if count_range and not count_range[0] <= n <= count_range[1]:
        warnings.append(
            StructureViolation(
                code="RENTANG_JENIS",
                message=(
                    f"Jumlah tahap {n} di luar rentang wajar untuk jenis "
                    f"{campaign_type.value} ({count_range[0]}-{count_range[1]} tahap)."
                ),
            )
        )

    # Cek paket bukti hanya bila struktur memuat informasi bukti; request
    # struktur-porsi-saja (semua evidence_types kosong) tidak dihukum.
    if any(m.evidence_types for m in milestones):
        required = REQUIRED_EVIDENCE_BY_TYPE[campaign_type]
        for m in ordered:
            missing = required - set(m.evidence_types)
            if missing:
                violations.append(
                    StructureViolation(
                        code="BUKTI_WAJIB",
                        message=(
                            f"Milestone #{m.order} kekurangan bukti wajib jenis "
                            f"{campaign_type.value}: {', '.join(sorted(e.value for e in missing))}."
                        ),
                    )
                )
        if campaign_type in _HANDOVER_REQUIRED_TYPES and EvidenceType.SERAH_TERIMA not in last.evidence_types:
            violations.append(
                StructureViolation(
                    code="SERAH_TERIMA_FINAL",
                    message=(
                        f"Jenis {campaign_type.value} wajib menyertakan bukti SERAH_TERIMA "
                        "pada milestone final."
                    ),
                )
            )

    return StructureCheckResult(valid=not violations, violations=violations, warnings=warnings)
