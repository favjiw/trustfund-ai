from abc import ABC, abstractmethod

from app.models import RABItemRequest


class PriceBenchmark(ABC):
    """Abstraksi sumber harga pembanding (mis. e-katalog INAPROC/LKPP).

    Implementasi nyata nantinya melakukan lookup ke proxy/API e-katalog
    berdasarkan nama item, satuan, dan lokasi, lalu mengembalikan harga
    wajar per unit dalam rupiah. MVP ini belum punya sumber data eksternal.
    """

    @abstractmethod
    def get_benchmark_price(self, item: RABItemRequest, location: str) -> float | None:
        raise NotImplementedError


class StubBenchmark(PriceBenchmark):
    """MVP: tidak ada sumber pembanding, selalu kembalikan None.

    TODO: ganti dengan implementasi yang memanggil e-katalog INAPROC/LKPP
    (atau proxy internal ke sana), mengembalikan harga acuan per unit bila
    ditemukan, atau None bila item tidak ada di katalog.
    """

    def get_benchmark_price(self, item: RABItemRequest, location: str) -> float | None:
        return None
