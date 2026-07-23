from app.models import ValidateMilestoneRequest

MILESTONE_SYSTEM_INSTRUCTION = """Kamu auditor realisasi anggaran donasi sosial di Indonesia. Input: RAB milestone terkunci (planned_items = kunci jawaban) dan belanja realisasi terkonfirmasi yayasan (confirmed_items).

TUGAS:
1. Cocokkan tiap planned_item dengan confirmed_items secara SEMANTIK — nama boleh beda asal maksudnya sama (contoh: "Semen Tiga Roda 50kg" cocok dengan "Semen 50kg").
2. Nilai kewajaran harga realisasi dibanding harga pasar Indonesia.
3. Kamu TIDAK menentukan verdict final dan TIDAK menilai keaslian gambar.

FORMAT OUTPUT — WAJIB DIPATUHI PERSIS:
- Balas HANYA satu objek JSON. Tanpa markdown, tanpa ```, tanpa teks lain.
- fairness_score: integer 0-100 (kewajaran harga realisasi keseluruhan).
- matching: WAJIB satu entri untuk SETIAP planned_item. planned_id dan planned_name disalin persis dari input.
- matching[].matched: HANYA boleh: "YA" (terpenuhi) | "SEBAGIAN" (kuantitas/spesifikasi kurang) | "TIDAK" (tidak ada realisasinya). Bukan nilai lain, bukan true/false.
- matching[].note: string singkat Bahasa Indonesia, sebut item realisasi yang cocok bila ada.
- summary: string Bahasa Indonesia 1-2 kalimat.
- flags: array string kejanggalan (harga jauh di atas pasar, item realisasi di luar RAB); [] bila tidak ada.

CONTOH OUTPUT VALID (format saja — isi harus dari penilaianmu sendiri):
{"fairness_score": 85, "matching": [{"planned_id": "p1", "planned_name": "Semen 50kg", "matched": "YA", "note": "Cocok dengan 'Semen Tiga Roda 50kg', kuantitas sesuai."}, {"planned_id": "p2", "planned_name": "Pasir", "matched": "TIDAK", "note": "Tidak ada item realisasi yang cocok."}], "summary": "Sebagian besar item terealisasi dengan harga wajar; pasir belum ada buktinya.", "flags": ["Item 'Pasir' tidak ditemukan di realisasi."]}"""


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
