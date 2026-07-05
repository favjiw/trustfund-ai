from app.models import ValidateRABRequest

SYSTEM_INSTRUCTION = (
    "Kamu auditor RAB donasi sosial di Indonesia. Nilai kewajaran harga tiap item "
    "dibanding harga pasar wajar di Indonesia, dengan mempertimbangkan lokasi dan "
    "jenis proyek. Jika ada benchmark_price, jadikan acuan utama. Jika tidak ada, "
    "nilai berbasis pengetahuan umum dan tandai confidence RENDAH. Deteksi item yang "
    "tidak relevan dengan tujuan proyek. Jawab HANYA dalam JSON sesuai schema. "
    "Gunakan Bahasa Indonesia untuk semua teks alasan."
)


def build_prompt(payload: ValidateRABRequest, benchmarks: dict[str, float | None]) -> str:
    lines: list[str] = []

    lines.append("## Informasi Kampanye")
    lines.append(f"Jenis kampanye: {payload.campaign_type.value}")
    lines.append(f"Judul: {payload.campaign_title}")
    lines.append(f"Deskripsi: {payload.campaign_description}")
    lines.append(f"Lokasi: {payload.location}")
    lines.append("")
    lines.append("## Daftar Item RAB")

    for item in payload.items:
        benchmark_price = benchmarks.get(item.id)
        benchmark_text = f"Rp{benchmark_price:,.0f}" if benchmark_price is not None else "tidak tersedia"
        lines.append(
            f"- id={item.id} | nama=\"{item.name}\" | kuantitas={item.quantity} {item.unit} | "
            f"harga_satuan=Rp{item.unit_price:,.0f} | subtotal=Rp{item.subtotal:,.0f} | "
            f"benchmark_price={benchmark_text}".replace(",", ".")
        )

    lines.append("")
    lines.append("## Instruksi")
    lines.append(
        "Untuk setiap item di atas, nilai kewajaran harganya (fairness), berikan alasan "
        "singkat (reason) yang bisa ditelusuri, dan tingkat keyakinan (confidence). "
        "Beri juga skor kewajaran keseluruhan RAB dari 0-100 (overall_score), ringkasan "
        "singkat (summary), dan daftar flags untuk hal-hal yang perlu diperhatikan "
        "(misalnya item yang tidak relevan dengan tujuan proyek, atau pola mark-up "
        "yang mencurigakan). Jawab hanya dengan JSON sesuai schema yang diberikan."
    )

    return "\n".join(lines)
