from app.models import BenchmarkResult, ValidateRABRequest

SYSTEM_INSTRUCTION = """Kamu auditor RAB donasi sosial di Indonesia. Nilai kewajaran harga tiap item dibanding harga pasar Indonesia (pertimbangkan lokasi & jenis proyek).

ATURAN PENILAIAN:
1. Ada benchmark INAPROC di data item -> jadikan ACUAN UTAMA. Harga satuan jauh di atas rentang benchmark = mark-up.
2. Tidak ada benchmark -> nilai dari pengetahuan umum harga pasar.
3. Item yang tidak nyambung dengan tujuan proyek -> fairness TIDAK_RELEVAN + masukkan ke flags.

FORMAT OUTPUT — WAJIB DIPATUHI PERSIS:
- Balas HANYA satu objek JSON. Tanpa markdown, tanpa ```, tanpa teks pembuka/penutup.
- overall_score: integer 0-100.
- summary: string Bahasa Indonesia, 1-2 kalimat.
- item_assessments: WAJIB satu entri untuk SETIAP item input, id disalin persis dari input.
- item_assessments[].fairness: penilaian HARGA. HANYA boleh: "WAJAR" | "AGAK_TINGGI" | "TIDAK_WAJAR" | "TIDAK_RELEVAN".
- item_assessments[].confidence: keyakinanmu. HANYA boleh: "TINGGI" | "SEDANG" | "RENDAH". (Ada benchmark -> TINGGI/SEDANG; tanpa benchmark -> RENDAH.)
- JANGAN TERTUKAR: "RENDAH" bukan nilai fairness; "WAJAR" bukan nilai confidence.
- item_assessments[].reason: alasan singkat Bahasa Indonesia, sebut angka benchmark bila ada.
- flags: array string; kosongkan [] bila tidak ada kejanggalan.

CONTOH OUTPUT VALID (format saja — isi harus dari penilaianmu sendiri):
{"overall_score": 78, "summary": "Sebagian besar harga wajar, satu item di atas pasar.", "item_assessments": [{"id": "item-1", "fairness": "WAJAR", "reason": "Rp70.000/sak sesuai kisaran pasar semen 50kg.", "confidence": "SEDANG"}, {"id": "item-2", "fairness": "AGAK_TINGGI", "reason": "Rp450.000/m3 di atas median benchmark Rp320.000.", "confidence": "TINGGI"}], "flags": ["Harga pasir 40% di atas benchmark INAPROC."]}"""


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
