OCR_PARSE_SYSTEM_INSTRUCTION = (
    "Kamu asisten pengisian form untuk nota/kuitansi belanja di Indonesia. Tugasmu "
    "HANYA merapikan teks hasil OCR menjadi daftar item terstruktur (nama, kuantitas, "
    "harga satuan, subtotal dalam rupiah). Kamu TIDAK menilai kewajaran harga atau "
    "kecocokan dengan RAB. Bersihkan format rupiah ('Rp', titik ribuan) menjadi angka. "
    "Nilai yang tidak yakin isi null dan tandai confidence RENDAH — JANGAN mengarang "
    "angka. Jawab HANYA dalam JSON sesuai schema. Gunakan Bahasa Indonesia untuk teks."
)

OCR_VISION_SYSTEM_INSTRUCTION = (
    "Kamu asisten pengisian form untuk nota/kuitansi belanja di Indonesia. Baca gambar "
    "nota yang diberikan, salin teks mentahnya, lalu susun daftar item terstruktur "
    "(nama, kuantitas, harga satuan, subtotal dalam rupiah). Kamu TIDAK menilai "
    "kewajaran harga atau kecocokan dengan RAB. Bersihkan format rupiah ('Rp', titik "
    "ribuan) menjadi angka. Nilai yang tidak terbaca/tidak yakin isi null dan tandai "
    "confidence RENDAH — JANGAN mengarang angka. Jika sebagian tulisan tidak terbaca, "
    "catat di warnings. Jawab HANYA dalam JSON sesuai schema. Gunakan Bahasa Indonesia."
)


def _hint_section(hint_items: list[str]) -> str:
    if not hint_items:
        return ""
    lines = ["", "## Petunjuk (item RAB milestone terkait, gunakan untuk mengenali nama item)"]
    lines.extend(f"- {hint}" for hint in hint_items)
    return "\n".join(lines)


def build_ocr_parse_prompt(raw_text: str, hint_items: list[str]) -> str:
    return (
        "## Teks mentah hasil OCR nota\n"
        "```\n"
        f"{raw_text}\n"
        "```\n"
        f"{_hint_section(hint_items)}\n\n"
        "## Instruksi\n"
        "Susun teks OCR di atas menjadi daftar item belanja (suggested_items) dengan "
        "name, quantity, unit_price, subtotal (angka rupiah bersih), dan confidence "
        "per item. Jika nota mencantumkan total, isi detected_total. Catat masalah "
        "keterbacaan di warnings."
    )


def build_ocr_vision_prompt(hint_items: list[str]) -> str:
    return (
        "Baca gambar nota terlampir."
        f"{_hint_section(hint_items)}\n\n"
        "## Instruksi\n"
        "Salin teks nota ke raw_text, lalu susun daftar item belanja (suggested_items) "
        "dengan name, quantity, unit_price, subtotal (angka rupiah bersih), dan "
        "confidence per item. Jika nota mencantumkan total, isi detected_total. Catat "
        "bagian yang tidak terbaca di warnings."
    )
