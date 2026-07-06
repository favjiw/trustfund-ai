import re

# Kata satuan/ukuran/kemasan yang tidak berguna sebagai keyword pencarian.
_STOPWORDS = {
    "kg", "gr", "gram", "liter", "ltr", "l", "ml", "m", "m2", "m3", "cm", "mm",
    "inch", "in", "pcs", "pcs.", "unit", "buah", "set", "pack", "pak", "sak",
    "zak", "dus", "box", "roll", "rol", "batang", "lembar", "keping", "rit",
    "ukuran", "uk", "tipe", "type", "merk", "merek", "per", "isi", "warna",
}


def heuristic_keyword(name: str) -> str:
    """Turunkan keyword pencarian INAPROC dari nama item RAB tanpa LLM.

    Contoh: 'Semen Portland 40kg Tiga Roda' -> 'semen portland'.
    Buang angka, satuan, dan tanda baca; ambil 1-2 kata bermakna pertama.
    """
    lowered = name.lower()
    # Buang angka + satuan yang menempel (mis. '40kg', '5m2') dan tanda baca.
    lowered = re.sub(r"\d+([.,]\d+)?\s*[a-z]*", " ", lowered)
    lowered = re.sub(r"[^a-z\s]", " ", lowered)

    words = [w for w in lowered.split() if w and w not in _STOPWORDS]
    if not words:
        # Fallback terakhir: kata pertama dari nama asli.
        fallback = re.sub(r"[^a-zA-Z]", " ", name).split()
        return fallback[0].lower() if fallback else name.strip().lower()

    return " ".join(words[:2])
