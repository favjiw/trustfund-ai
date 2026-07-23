from app.models import BenchmarkResult, ValidateRABRequest

SYSTEM_INSTRUCTION = (
    "Kamu auditor RAB donasi sosial di Indonesia. Nilai kewajaran harga tiap item "
    "dibanding harga pasar wajar di Indonesia, dengan mempertimbangkan lokasi dan "
    "jenis proyek. Jika ada benchmark harga e-katalog INAPROC, JADIKAN ACUAN UTAMA: "
    "harga satuan yang jauh di atas rentang benchmark = mark-up. Jika benchmark "
    "tidak tersedia, nilai berbasis pengetahuan umum. Deteksi juga item yang tidak "
    "relevan dengan tujuan proyek.\n\n"
    "DUA FIELD ENUM BERBEDA — JANGAN TERTUKAR:\n"
    "- fairness = penilaian HARGA item. Nilai yang sah HANYA: WAJAR, AGAK_TINGGI, "
    "TIDAK_WAJAR, TIDAK_RELEVAN.\n"
    "- confidence = seberapa yakin kamu pada penilaianmu. Nilai yang sah HANYA: "
    "TINGGI, SEDANG, RENDAH. (Ada benchmark → TINGGI/SEDANG; tanpa benchmark → RENDAH.)\n"
    "RENDAH bukan nilai fairness; WAJAR bukan nilai confidence.\n\n"
    "Jawab HANYA dalam JSON sesuai schema. Gunakan Bahasa Indonesia untuk semua "
    "teks alasan."
)


def _rp(amount: float) -> str:
    """Format rupiah dengan pemisah ribuan titik (gaya Indonesia)."""
    return f"Rp{amount:,.0f}".replace(",", ".")


def _benchmark_text(result: BenchmarkResult | None) -> str:
    if result is None:
        return "benchmark INAPROC: tidak tersedia"
    samples = f"; contoh: {', '.join(result.sample_names)}" if result.sample_names else ""
    return (
        f"benchmark INAPROC (keyword '{result.keyword}', {result.sample_count} produk): "
        f"median {_rp(result.median_price)}, rentang {_rp(result.min_price)}–"
        f"{_rp(result.max_price)}{samples}"
    )


def build_prompt(payload: ValidateRABRequest, benchmarks: dict[str, BenchmarkResult | None]) -> str:
    lines: list[str] = []

    lines.append("## Informasi Kampanye")
    lines.append(f"Jenis kampanye: {payload.campaign_type.value}")
    lines.append(f"Judul: {payload.campaign_title or '-'}")
    lines.append(f"Deskripsi: {payload.campaign_description or '-'}")
    lines.append(f"Lokasi: {payload.location or '-'}")
    lines.append("")
    lines.append("## Daftar Item RAB")

    for item in payload.items:
        bench = _benchmark_text(benchmarks.get(item.id))
        lines.append(
            f"- id={item.id} | nama=\"{item.name}\" | kuantitas={item.quantity} {item.unit} | "
            f"harga_satuan={_rp(item.unit_price)} | subtotal={_rp(item.subtotal)} | {bench}"
        )

    lines.append("")
    lines.append("## Instruksi")
    lines.append(
        "Untuk setiap item di atas, nilai kewajaran harganya (fairness), berikan alasan "
        "singkat (reason) yang bisa ditelusuri (sebut angka benchmark bila ada), dan "
        "tingkat keyakinan (confidence). Beri juga skor kewajaran keseluruhan RAB dari "
        "0-100 (overall_score), ringkasan singkat (summary), dan daftar flags untuk "
        "hal-hal yang perlu diperhatikan (item tidak relevan dengan tujuan proyek, atau "
        "pola mark-up mencurigakan). Jawab hanya dengan JSON sesuai schema yang diberikan."
    )

    return "\n".join(lines)
