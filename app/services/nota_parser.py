import io
import logging

from app.models import ParseNotaResponse
from app.prompts.nota_prompt import NOTA_SYSTEM_INSTRUCTION, build_nota_prompt
from app.services.llm_client import LLMClient, LLMClientError

logger = logging.getLogger(__name__)

_MIME_BY_FORMAT = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "BMP": "image/bmp",
    "TIFF": "image/tiff",
}


class InvalidImageError(Exception):
    """Input bukan gambar yang valid."""


class NotaParseError(Exception):
    """Vision LLM gagal membaca nota (jaringan/model/schema)."""


def sniff_image_mime(image_bytes: bytes) -> str:
    """Validasi bahwa bytes adalah gambar dan kembalikan MIME type-nya."""
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            img.verify()
            fmt = img.format
    except (UnidentifiedImageError, OSError) as exc:
        raise InvalidImageError("File yang dikirim bukan gambar yang valid") from exc

    return _MIME_BY_FORMAT.get(fmt or "", "image/jpeg")


class NotaParser:
    """Foto nota -> JSON terstruktur via vision LLM. Gagal = error eksplisit
    (502), BUKAN hasil kosong diam-diam — backend harus tahu bedanya 'nota tak
    terbaca' dan 'service gagal'."""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm_client = llm_client

    def parse(self, image_bytes: bytes, hint_items: list[str] | None = None) -> ParseNotaResponse:
        mime_type = sniff_image_mime(image_bytes)
        try:
            return self._llm_client.generate_structured(
                NOTA_SYSTEM_INSTRUCTION,
                build_nota_prompt(hint_items or []),
                ParseNotaResponse,
                image=(image_bytes, mime_type),
            )
        except LLMClientError as exc:
            raise NotaParseError(str(exc)) from exc
