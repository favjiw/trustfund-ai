import io

from PIL import Image, ImageChops, UnidentifiedImageError

from app.config import Settings
from app.models import Suspicion


class InvalidForensicImageError(Exception):
    """Input bukan gambar yang valid untuk analisis ELA."""


def compute_ela(image_bytes: bytes, settings: Settings) -> tuple[Suspicion, dict[str, float]]:
    """Error Level Analysis deterministik (tanpa training).

    Gambar di-save ulang sebagai JPEG (quality dari ENV), lalu selisih
    per-pixel diukur. Area yang pernah diedit/ditempel cenderung punya error
    level berbeda dari sekitarnya. Ini SINYAL pendukung untuk review manusia,
    BUKAN vonis keaslian gambar.
    """
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            original = img.convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise InvalidForensicImageError("File yang dikirim bukan gambar yang valid") from exc

    buffer = io.BytesIO()
    original.save(buffer, format="JPEG", quality=settings.ela_jpeg_quality)
    buffer.seek(0)
    resaved = Image.open(buffer).convert("RGB")

    diff = ImageChops.difference(original, resaved)

    # Ukur intensitas selisih: rata-rata per kanal + nilai maksimum.
    stat_sums = [0, 0, 0]
    histogram = diff.histogram()  # 3 kanal x 256 bin
    total_pixels = diff.size[0] * diff.size[1]
    max_diff = 0
    for channel in range(3):
        bins = histogram[channel * 256 : (channel + 1) * 256]
        stat_sums[channel] = sum(value * count for value, count in enumerate(bins))
        channel_max = max((value for value, count in enumerate(bins) if count > 0), default=0)
        max_diff = max(max_diff, channel_max)

    mean_diff = sum(stat_sums) / (3 * total_pixels) if total_pixels else 0.0

    if mean_diff >= settings.ela_tinggi_threshold:
        suspicion = Suspicion.TINGGI
    elif mean_diff >= settings.ela_sedang_threshold:
        suspicion = Suspicion.SEDANG
    else:
        suspicion = Suspicion.RENDAH

    metrics = {
        "mean_diff": round(mean_diff, 3),
        "max_diff": float(max_diff),
        "jpeg_quality": float(settings.ela_jpeg_quality),
    }
    return suspicion, metrics
