from app.config import Settings
from app.models import CampaignType, PlanMilestonesRequest

PLANNER_SYSTEM_INSTRUCTION = (
    "Kamu AI Planner platform donasi TrustFund. Tugasmu menyusun DRAF struktur "
    "milestone pencairan dana dari RAB dan konteks kampanye. Dana donatur dikunci "
    "escrow dan hanya cair bertahap per milestone setelah bukti divalidasi, sehingga "
    "struktur harus mengikuti prinsip retensi progresif: porsi kecil di depan, porsi "
    "terbesar ditahan di milestone final sebagai insentif menyelesaikan proyek. "
    "Untuk tiap milestone tulis judul singkat, porsi persen, definition-of-done yang "
    "konkret dan bisa diverifikasi, jenis bukti wajib, item RAB yang dikerjakan di "
    "tahap itu (item_ids), dan ALASAN keputusanmu dalam Bahasa Indonesia (transparansi "
    "ke yayasan). Setiap item RAB harus masuk ke TEPAT SATU milestone. Jawab HANYA "
    "dalam JSON sesuai schema."
)

_EVIDENCE_HINT_BY_TYPE = {
    CampaignType.PEMBANGUNAN: (
        "NOTA + FOTO_GEOTAG tiap milestone (nota material + foto progres geotag); "
        "LAPORAN opsional."
    ),
    CampaignType.PENGADAAN_BARANG: (
        "NOTA + FOTO_GEOTAG tiap milestone; SERAH_TERIMA WAJIB pada milestone final."
    ),
    CampaignType.ALAT_KESEHATAN: (
        "NOTA + FOTO_GEOTAG tiap milestone; SERAH_TERIMA WAJIB pada milestone final."
    ),
    CampaignType.REKONSTRUKSI: (
        "NOTA + FOTO_GEOTAG tiap milestone (foto before/after geotag); LAPORAN opsional."
    ),
}

_COUNT_HINT_BY_TYPE = {
    CampaignType.PEMBANGUNAN: "3-5 tahap (per tahap konstruksi: fondasi, struktur, finishing)",
    CampaignType.PENGADAAN_BARANG: "2-3 tahap (pesan, terima, serah-terima)",
    CampaignType.ALAT_KESEHATAN: "2-3 tahap (pesan, terima, serah-terima)",
    CampaignType.REKONSTRUKSI: "3-5 tahap (per tahap pemulihan)",
}


def build_planner_prompt(
    payload: PlanMilestonesRequest,
    total_amount: float,
    settings: Settings,
    feedback: list[str] | None = None,
) -> str:
    lines: list[str] = []

    lines.append("## Konteks kampanye")
    lines.append(f"Jenis: {payload.campaign_type.value}")
    lines.append(f"Judul: {payload.campaign_title}")
    lines.append(f"Deskripsi: {payload.campaign_description}")
    lines.append(f"Lokasi: {payload.location}")
    if payload.duration_days:
        lines.append(f"Durasi rencana: {payload.duration_days} hari")
    lines.append(f"Total RAB: Rp{total_amount:,.0f}")
    lines.append("")

    lines.append("## Item RAB")
    for item in payload.items:
        lines.append(
            f"- id={item.id} | nama=\"{item.name}\" | {item.quantity} {item.unit} | "
            f"harga_satuan=Rp{item.unit_price:,.0f} | subtotal=Rp{item.subtotal:,.0f}"
        )
    lines.append("")

    lines.append("## Pagar sistem (WAJIB dipatuhi — draf yang melanggar akan ditolak)")
    lines.append(
        f"1. Jumlah milestone {settings.milestone_min_count}-{settings.milestone_max_count}; "
        f"untuk jenis ini idealnya {_COUNT_HINT_BY_TYPE[payload.campaign_type]}."
    )
    lines.append(f"2. Milestone pertama (DP/modal awal) maksimal {settings.dp_max_pct:.0f}%.")
    lines.append(f"3. Tidak ada milestone yang melebihi {settings.milestone_max_pct:.0f}%.")
    lines.append(
        f"4. Porsi milestone FINAL harus yang TERBESAR (retensi), idealnya "
        f">= {settings.final_retention_min_pct:.0f}%."
    )
    lines.append("5. Total seluruh porsi tepat 100%.")
    lines.append(f"6. Paket bukti: {_EVIDENCE_HINT_BY_TYPE[payload.campaign_type]}")
    lines.append("7. Setiap item RAB masuk ke tepat satu milestone (tidak ada yang hilang/dobel).")

    if feedback:
        lines.append("")
        lines.append("## PERBAIKAN — draf sebelumnya melanggar pagar berikut, susun ulang:")
        for note in feedback:
            lines.append(f"- {note}")

    lines.append("")
    lines.append(
        "Susun draf milestone terbaik untuk kampanye ini. Field percentage berupa "
        "angka persen (jumlah semua = 100). Jawab hanya JSON sesuai schema."
    )

    return "\n".join(lines)
