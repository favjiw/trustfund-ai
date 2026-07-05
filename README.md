# TrustFund AI - RAB Validator

Microservice Python + FastAPI yang memvalidasi kewajaran RAB (Rencana Anggaran
Biaya) sebuah kampanye donasi di platform TrustFund (crowdfunding donasi
berbasis blockchain + AI). Service ini **stateless**: menerima RAB, memberi
penilaian kewajaran per item + skor keseluruhan + alasan, lalu selesai. Service
ini **tidak** menyentuh database atau blockchain sama sekali — itu tanggung
jawab backend Node.js yang memanggilnya.

> **Penting — endpoint internal.** `/api/v1/validate-rab` bukan endpoint
> publik. Endpoint ini hanya boleh dipanggil oleh backend Node.js TrustFund,
> dengan menyertakan header `X-Internal-Token` yang cocok dengan ENV
> `INTERNAL_TOKEN` di service ini. Jangan expose service ini langsung ke
> internet/klien akhir.

## Fitur

- Menilai kewajaran harga tiap item RAB (deteksi mark-up / harga tidak wajar)
  dan kelogisan item terhadap tujuan proyek, menggunakan Google Gemini.
- Verdict akhir (`WAJAR` / `PERLU_REVIEW` / `MENCURIGAKAN`) dihitung oleh kode
  berdasarkan `overall_score`, bukan diserahkan sepenuhnya ke LLM.
- Abstraksi `PriceBenchmark` sudah disiapkan untuk diisi nanti dengan sumber
  harga e-katalog (INAPROC/LKPP). MVP ini belum punya sumber pembanding
  eksternal, jadi `benchmark_price` selalu `null` dan LLM menilai berbasis
  pengetahuan umum (dengan `confidence: RENDAH`).
- Semua respons Gemini dipaksa JSON terstruktur (response schema) dan
  di-parse dengan aman; kegagalan LLM menghasilkan error terstruktur (HTTP
  502), bukan crash.

## Struktur proyek

```
app/
  main.py                 # FastAPI app, /health, /api/v1/validate-rab, CORS, auth, error handler
  config.py               # baca ENV (pydantic-settings)
  models.py               # semua Pydantic schema (request/response)
  services/
    llm_client.py          # wrapper Gemini: kirim prompt+schema, kembalikan hasil tervalidasi
    benchmark.py           # PriceBenchmark (abstrak) + StubBenchmark (MVP, selalu None)
    validator.py            # orkestrasi: benchmark -> prompt -> LLM -> skor->verdict -> response
  prompts/
    rab_prompt.py           # system instruction + build_prompt()
tests/
  test_validator.py         # test dengan LLM di-mock (tidak memanggil API asli)
```

## Menjalankan secara lokal

1. Buat virtual environment dan install dependency:

   ```bash
   python -m venv .venv
   source .venv/bin/activate   # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. Salin `.env.example` menjadi `.env` dan isi nilainya:

   ```bash
   cp .env.example .env
   ```

   Minimal isi `GEMINI_API_KEY` dengan API key Gemini yang valid.

3. Jalankan service:

   ```bash
   uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```

4. Cek `/health`:

   ```bash
   curl http://localhost:8000/health
   ```

## Menjalankan dengan Docker

```bash
docker build -t trustfund-ai .
docker run --rm -p 8000:8000 --env-file .env trustfund-ai
```

## Menjalankan test

Test menggunakan LLM client yang di-mock (`FakeLLMClient`), jadi tidak
memanggil Gemini API asli dan tidak butuh `GEMINI_API_KEY` valid:

```bash
pytest
```

## Contoh curl

Set token internal (samakan dengan `INTERNAL_TOKEN` di `.env`):

```bash
export INTERNAL_TOKEN=change_me_to_a_long_random_secret
```

### Request

```bash
curl -X POST http://localhost:8000/api/v1/validate-rab \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" \
  -d '{
    "campaign_id": "camp-123",
    "campaign_type": "PEMBANGUNAN",
    "campaign_title": "Pembangunan Sumur Bor Desa Cikembang",
    "campaign_description": "Membangun sumur bor untuk menyediakan akses air bersih bagi 50 KK di desa terpencil.",
    "location": "Bandung, Jawa Barat",
    "items": [
      {
        "id": "item-1",
        "name": "Semen Portland 40kg",
        "quantity": 50,
        "unit": "sak",
        "unit_price": 65000,
        "subtotal": 3250000
      },
      {
        "id": "item-2",
        "name": "Pompa Air Submersible",
        "quantity": 1,
        "unit": "unit",
        "unit_price": 4500000,
        "subtotal": 4500000
      },
      {
        "id": "item-3",
        "name": "Smartphone flagship untuk koordinator",
        "quantity": 1,
        "unit": "unit",
        "unit_price": 18000000,
        "subtotal": 18000000
      }
    ]
  }'
```

### Response (contoh)

```json
{
  "campaign_id": "camp-123",
  "overall_score": 42,
  "verdict": "MENCURIGAKAN",
  "summary": "Sebagian besar item wajar, namun ada item yang tidak relevan dengan tujuan proyek dan berharga sangat tinggi.",
  "total_declared": 25750000,
  "item_assessments": [
    {
      "id": "item-1",
      "name": "Semen Portland 40kg",
      "unit_price": 65000,
      "benchmark_price": null,
      "fairness": "WAJAR",
      "reason": "Harga sesuai kisaran pasar semen di Jawa Barat.",
      "confidence": "RENDAH"
    },
    {
      "id": "item-2",
      "name": "Pompa Air Submersible",
      "unit_price": 4500000,
      "benchmark_price": null,
      "fairness": "WAJAR",
      "reason": "Harga wajar untuk pompa submersible kapasitas standar.",
      "confidence": "RENDAH"
    },
    {
      "id": "item-3",
      "name": "Smartphone flagship untuk koordinator",
      "unit_price": 18000000,
      "benchmark_price": null,
      "fairness": "TIDAK_RELEVAN",
      "reason": "Item ini tidak relevan dengan tujuan pembangunan sumur bor dan berpotensi penyalahgunaan dana.",
      "confidence": "RENDAH"
    }
  ],
  "flags": [
    "Item 'Smartphone flagship untuk koordinator' tidak relevan dengan tujuan proyek pembangunan sumur bor."
  ]
}
```

## Daftar ENV

| ENV                     | Wajib | Default                 | Keterangan                                                            |
|-------------------------|-------|--------------------------|-------------------------------------------------------------------------|
| `GEMINI_API_KEY`         | Ya    | -                         | API key Google Gemini.                                                  |
| `GEMINI_MODEL`           | Tidak | `gemini-2.5-flash`        | Nama model Gemini yang dipakai.                                         |
| `INTERNAL_TOKEN`         | Ya*   | -                         | Shared secret yang harus dikirim backend Node.js via `X-Internal-Token`. |
| `INTERNAL_AUTH_ENABLED`  | Tidak | `true`                    | Set `false` untuk menonaktifkan cek token (misal saat dev lokal).       |
| `CORS_ORIGINS`           | Tidak | `*`                       | Daftar origin yang diizinkan, dipisah koma, atau `*` untuk semua.        |

\* Wajib diisi bila `INTERNAL_AUTH_ENABLED=true` (default).

## Integrasi dengan backend Node.js

- Backend Node.js memanggil `POST /api/v1/validate-rab` dengan header
  `X-Internal-Token: <INTERNAL_TOKEN>` setiap kali perlu memvalidasi RAB
  sebuah kampanye (mis. saat kampanye diajukan/diedit).
- Service ini tidak menyimpan apa pun; backend Node.js yang bertanggung jawab
  menyimpan hasil (`overall_score`, `verdict`, `item_assessments`, `flags`)
  ke database dan/atau blockchain sesuai kebutuhan bisnis TrustFund.
- Jika Gemini gagal merespons atau mengeluarkan JSON di luar schema, service
  ini mengembalikan HTTP `502` dengan body `{"error": "llm_validation_failed",
  "detail": "..."}` — backend Node.js sebaiknya menangani ini sebagai
  kegagalan sementara (retry / tandai perlu review manual), bukan sebagai
  kampanye yang otomatis ditolak.
- `benchmark_price` saat ini selalu `null` (belum ada sumber e-katalog).
  Ketika sumber harga LKPP/INAPROC sudah tersedia, cukup implementasikan
  `PriceBenchmark` baru di `app/services/benchmark.py` dan suntikkan ke
  `RABValidator` pada `app/main.py` — tidak ada perubahan kontrak API yang
  diperlukan.
