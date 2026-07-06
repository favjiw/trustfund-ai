from app.models import ValidateMilestoneRequest

MILESTONE_SYSTEM_INSTRUCTION = (
    "Kamu auditor realisasi anggaran donasi sosial di Indonesia. Kamu menerima RAB "
    "milestone yang sudah dikunci (planned_items, kunci jawaban) dan daftar belanja "
    "realisasi yang sudah dikonfirmasi yayasan (confirmed_items). Tugasmu: "
    "(a) cocokkan tiap planned_item dengan confirmed_items secara SEMANTIK — nama "
    "boleh beda tapi maksud sama (mis. 'Semen Tiga Roda 50kg' cocok dengan 'Semen "
    "50kg'); tandai YA (terpenuhi), SEBAGIAN (kuantitas/spesifikasi kurang), atau "
    "TIDAK (tidak ada realisasinya). (b) Nilai kewajaran harga realisasi dibanding "
    "harga pasar Indonesia (fairness_score 0-100). (c) Tulis summary dan catatan "
    "Bahasa Indonesia yang bisa ditelusuri. Kamu TIDAK menentukan verdict final dan "
    "TIDAK menilai keaslian gambar. Jawab HANYA dalam JSON sesuai schema."
)


def build_milestone_prompt(payload: ValidateMilestoneRequest) -> str:
    lines: list[str] = []

    lines.append("## Konteks")
    lines.append(f"Jenis kampanye: {payload.campaign_type.value}")
    lines.append(f"Target nominal milestone: Rp{payload.milestone_target_amount:,.0f}")
    lines.append("")
    lines.append("## RAB milestone yang dikunci (planned_items — kunci jawaban)")
    for item in payload.planned_items:
        lines.append(
            f"- id={item.id} | nama=\"{item.name}\" | {item.quantity} {item.unit} | "
            f"harga_satuan=Rp{item.unit_price:,.0f} | subtotal=Rp{item.subtotal:,.0f}"
        )
    lines.append("")
    lines.append("## Realisasi terkonfirmasi yayasan (confirmed_items)")
    for idx, item in enumerate(payload.confirmed_items, start=1):
        lines.append(
            f"- #{idx} | nama=\"{item.name}\" | qty={item.quantity} | "
            f"harga_satuan=Rp{item.unit_price:,.0f} | subtotal=Rp{item.subtotal:,.0f}"
        )
    lines.append("")
    lines.append("## Instruksi")
    lines.append(
        "Untuk SETIAP planned_item, isi matching (planned_id, planned_name, matched "
        "YA/SEBAGIAN/TIDAK, note singkat menyebut item realisasi yang cocok bila ada). "
        "Beri fairness_score 0-100 untuk kewajaran harga realisasi secara keseluruhan, "
        "summary singkat, dan flags untuk kejanggalan (mis. harga realisasi jauh di "
        "atas pasar, item realisasi di luar RAB). Jawab hanya JSON sesuai schema."
    )

    return "\n".join(lines)
