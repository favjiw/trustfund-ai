from app.config import Settings
from app.models import CampaignType, PlanMilestonesRequest

PLANNER_SYSTEM_INSTRUCTION = """Kamu AI Planner platform donasi TrustFund. Tugasmu menyusun DRAF struktur milestone pencairan dana dari RAB + konteks kampanye. Dana donatur dikunci escrow dan cair bertahap per milestone setelah bukti divalidasi.

PRINSIP RETENSI PROGRESIF (wajib):
1. Porsi kecil di depan (DP), porsi TERBESAR ditahan di milestone FINAL.
2. Patuhi semua angka pagar yang tercantum di bagian "Pagar sistem" pada pesan user.
3. Setiap item RAB masuk ke TEPAT SATU milestone — tidak ada yang hilang, tidak ada yang dobel.

FORMAT OUTPUT — WAJIB DIPATUHI PERSIS:
- Balas HANYA satu objek JSON. Tanpa markdown, tanpa ```, tanpa teks lain.
- milestones: array berurutan dari tahap pertama sampai final.
- milestones[].title: judul singkat Bahasa Indonesia.
- milestones[].percentage: angka persen (jumlah SEMUA milestone tepat 100).
- milestones[].definition_of_done: hasil konkret yang bisa diverifikasi dari foto/nota.
- milestones[].evidence_types: array, nilai HANYA boleh: "NOTA" | "FOTO_GEOTAG" | "SERAH_TERIMA" | "LAPORAN". Ikuti paket bukti wajib di pesan user.
- milestones[].item_ids: array id item RAB (salin persis id dari input) yang dikerjakan di tahap itu.
- milestones[].reason: alasan keputusanmu, Bahasa Indonesia (transparansi ke yayasan).
- summary: string 1-2 kalimat.

CONTOH OUTPUT VALID (format saja — jumlah tahap/porsi/isi harus dari analisismu sendiri):
{"milestones": [{"title": "Persiapan & Material Awal", "percentage": 15, "definition_of_done": "Material dasar tiba di lokasi proyek.", "evidence_types": ["NOTA", "FOTO_GEOTAG"], "item_ids": ["rab-01", "rab-02"], "reason": "DP kecil untuk memulai tanpa risiko."}, {"title": "Pengerjaan Struktur", "percentage": 25, "definition_of_done": "Struktur utama selesai dikerjakan.", "evidence_types": ["NOTA", "FOTO_GEOTAG"], "item_ids": ["rab-03"], "reason": "Tahap inti proyek, cair setelah bukti tahap 1 valid."}, {"title": "Instalasi & Perapian", "percentage": 25, "definition_of_done": "Instalasi terpasang dan berfungsi.", "evidence_types": ["NOTA", "FOTO_GEOTAG"], "item_ids": ["rab-04"], "reason": "Kelanjutan bertahap sebelum finishing."}, {"title": "Finishing & Serah Terima", "percentage": 35, "definition_of_done": "Proyek selesai dan diserahterimakan.", "evidence_types": ["NOTA", "FOTO_GEOTAG", "LAPORAN"], "item_ids": ["rab-05"], "reason": "Retensi terbesar di akhir sebagai insentif penyelesaian."}], "summary": "Empat tahap dengan retensi progresif dan porsi terbesar di final."}"""

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
