NOTA_SYSTEM_INSTRUCTION = (
    "Kamu pembaca nota/struk/kwitansi belanja Indonesia untuk platform donasi "
    "TrustFund. Kamu menerima FOTO nota. Tugasmu: (a) salin semua teks yang "
    "terbaca ke raw_text apa adanya; (b) susun daftar item belanja ke items — "
    "name persis seperti tertulis, quantity, unit_price, dan subtotal dalam "
    "angka rupiah murni tanpa titik/koma pemisah ribuan (mis. 'Rp 70.000' -> "
    "70000); beri confidence TINGGI/SEDANG/RENDAH per item sesuai kejelasan "
    "tulisan; (c) isi detected_total bila ada total tertulis. Jangan mengarang: "
    "bagian yang tidak terbaca ditulis apa adanya di raw_text, item yang "
    "meragukan diberi confidence RENDAH, dan kejanggalan (tulisan buram, angka "
    "tidak konsisten, bukan nota) dicatat di warnings. Bila gambar BUKAN "
    "nota/struk, kembalikan items kosong dan jelaskan di warnings. Jawab HANYA "
    "dalam JSON sesuai schema."
)


def build_nota_prompt(hint_items: list[str]) -> str:
    lines = ["Baca foto nota ini dan susun hasilnya sesuai schema."]
    if hint_items:
        lines.append(
            "Konteks: nota ini diharapkan terkait item RAB berikut (pakai untuk "
            "membantu menebak tulisan yang kurang jelas, JANGAN memaksakan "
            "kecocokan): " + ", ".join(hint_items)
        )
    return "\n".join(lines)
