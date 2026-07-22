from app.config import Settings
from app.models import CampaignType, EvidenceType, StructureMilestoneInput
from app.services.structure_guard import check_structure


def make_settings(**overrides) -> Settings:
    values = {"llm_api_key": "dummy", "internal_token": "x"}
    values.update(overrides)
    return Settings(_env_file=None, **values)


def ms(order: int, pct: float, evidence: list[EvidenceType] | None = None) -> StructureMilestoneInput:
    return StructureMilestoneInput(order=order, title=f"Tahap {order}", percentage=pct, evidence_types=evidence or [])


def codes(result) -> set[str]:
    return {v.code for v in result.violations}


def warning_codes(result) -> set[str]:
    return {w.code for w in result.warnings}


def test_struktur_valid_lolos():
    result = check_structure(
        CampaignType.PEMBANGUNAN,
        [ms(1, 15), ms(2, 25), ms(3, 25), ms(4, 35)],
        make_settings(),
    )
    assert result.valid
    assert result.violations == []


def test_dp_melebihi_batas():
    result = check_structure(CampaignType.PEMBANGUNAN, [ms(1, 30), ms(2, 30), ms(3, 40)], make_settings())
    assert not result.valid
    assert "DP_MAKS" in codes(result)


def test_retensi_akhir_harus_terbesar():
    result = check_structure(CampaignType.PEMBANGUNAN, [ms(1, 10), ms(2, 40), ms(3, 25), ms(4, 25)], make_settings())
    assert not result.valid
    assert "RETENSI_AKHIR" in codes(result)


def test_porsi_maksimum_per_tahap():
    result = check_structure(CampaignType.PEMBANGUNAN, [ms(1, 10), ms(2, 40), ms(3, 50)], make_settings())
    assert not result.valid
    assert "PORSI_MAKS" in codes(result)


def test_jumlah_tahap_di_luar_batas():
    result = check_structure(CampaignType.PEMBANGUNAN, [ms(1, 100)], make_settings())
    assert "JUMLAH_TAHAP" in codes(result)

    tujuh = [ms(i, 10) for i in range(1, 7)] + [ms(7, 40)]
    result = check_structure(CampaignType.PEMBANGUNAN, tujuh, make_settings())
    assert "JUMLAH_TAHAP" in codes(result)


def test_total_persen_harus_100():
    result = check_structure(CampaignType.PEMBANGUNAN, [ms(1, 10), ms(2, 30), ms(3, 40)], make_settings())
    assert "TOTAL_PERSEN" in codes(result)


def test_order_duplikat():
    result = check_structure(CampaignType.PEMBANGUNAN, [ms(1, 10), ms(1, 50), ms(3, 40)], make_settings())
    assert "ORDER_DUPLIKAT" in codes(result)


def test_retensi_kecil_hanya_warning():
    # Final tetap yang terbesar, tapi di bawah ambang ideal 20% -> warning saja.
    result = check_structure(
        CampaignType.PENGADAAN_BARANG,
        [ms(1, 15), ms(2, 17), ms(3, 17), ms(4, 17), ms(5, 15), ms(6, 19)],
        make_settings(),
    )
    assert result.valid
    assert "RETENSI_AKHIR_KECIL" in warning_codes(result)


def test_rentang_jenis_hanya_warning():
    result = check_structure(
        CampaignType.PENGADAAN_BARANG,
        [ms(1, 15), ms(2, 20), ms(3, 25), ms(4, 40)],
        make_settings(),
    )
    assert result.valid
    assert "RENTANG_JENIS" in warning_codes(result)


def test_bukti_wajib_dan_serah_terima_final():
    lengkap = [EvidenceType.NOTA, EvidenceType.FOTO_GEOTAG]
    result = check_structure(
        CampaignType.PENGADAAN_BARANG,
        [
            ms(1, 15, lengkap),
            ms(2, 40, [EvidenceType.FOTO_GEOTAG]),  # tanpa NOTA
            ms(3, 45, lengkap),  # final tanpa SERAH_TERIMA
        ],
        make_settings(milestone_max_pct=50.0),
    )
    assert not result.valid
    assert "BUKTI_WAJIB" in codes(result)
    assert "SERAH_TERIMA_FINAL" in codes(result)


def test_bukti_dilewati_bila_struktur_porsi_saja():
    result = check_structure(
        CampaignType.PENGADAAN_BARANG,
        [ms(1, 15), ms(2, 40), ms(3, 45)],
        make_settings(milestone_max_pct=50.0),
    )
    assert result.valid
