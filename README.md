# TrustFund AI Service

Microservice Python + FastAPI untuk platform TrustFund (crowdfunding donasi
berbasis blockchain + AI). Service ini **stateless**: menilai dan memberi skor,
lalu selesai. Service ini **tidak** menyentuh database atau blockchain —
itu tanggung jawab backend Node.js yang memanggilnya.

Kemampuan:

| Endpoint                        | Sifat                | Fungsi                                                        |
|---------------------------------|-----------------------|----------------------------------------------------------------|
| `POST /evaluate-rab`              | Wajib, menentukan     | **Kontrak backend NexTrust** — evaluasi RAB, hasil disimpan ke tabel `RabCheck` |
| `POST /api/v1/validate-rab`       | Wajib, menentukan     | Versi kaya dari evaluasi RAB (detail per item + verdict)        |
| `POST /api/v1/ocr-assist`         | Opsional, best-effort | Menyarankan isian form dari foto nota (dikoreksi manusia)      |
| `POST /api/v1/validate-milestone` | Wajib, menentukan     | Menilai bukti realisasi milestone yang sudah dikonfirmasi yayasan |
| `POST /api/v1/forensic-ela`       | Opsional              | Menghitung sinyal ELA satu foto (pendukung validate-milestone) |

Penilaian harga RAB memakai **benchmark e-katalog INAPROC** sebagai acuan bila
diaktifkan (lihat bagian *Benchmark INAPROC* di bawah). Enum kategori kampanye
selaras dengan `CampaignCategory` di skema Prisma backend: `PEMBANGUNAN`,
`PENGADAAN_BARANG`, `ALAT_KESEHATAN`, `REKONSTRUKSI`.

> **Penting — endpoint internal.** Semua endpoint hanya boleh dipanggil oleh
> backend Node.js TrustFund dengan header `X-Internal-Token` yang cocok dengan
> ENV `INTERNAL_TOKEN`. Jangan expose service ini langsung ke internet.

> **Prinsip inti OCR:** OCR BUKAN input utama. `/ocr-assist` hanya memberi
> SARAN isian; yayasan mengoreksi/mengisi manual di web; `/validate-milestone`
> bekerja atas data TERKONFIRMASI yayasan, BUKAN hasil OCR mentah. Validasi
> tidak bergantung pada keberhasilan OCR.

## Struktur proyek

```
app/
  main.py                        # FastAPI app, routes, CORS, auth, error handler
  config.py                      # baca ENV (pydantic-settings)
  models.py                      # semua Pydantic schema (request/response)
  services/
    llm_client.py                 # wrapper Gemini: prompt+schema → hasil tervalidasi (teks & vision)
    benchmark.py                  # PriceBenchmark (abstrak) + StubBenchmark (MVP, selalu None)
    validator.py                  # Validator RAB: benchmark → prompt → LLM → skor→verdict
    ocr_service.py                # OCR assist: PaddleOCR + Gemini structuring + vision fallback
    forensic.py                   # ELA (Error Level Analysis) deterministik
    milestone_validator.py         # Validator bukti milestone: 4 lapis + verdict di kode
  prompts/
    rab_prompt.py                  # prompt Validator RAB
    ocr_parse_prompt.py            # prompt strukturisasi teks OCR / baca nota (vision)
    milestone_prompt.py            # prompt matching semantik + kewajaran realisasi
tests/                           # semua test dengan LLM & OCR di-mock
```

## Menjalankan secara lokal

```bash
python -m venv .venv
source .venv/Scripts/activate     # Git Bash; PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt   # PaddleOCR besar; lihat catatan deploy
cp .env.example .env              # isi GEMINI_API_KEY (dan INTERNAL_TOKEN)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Tanpa PaddleOCR terpasang, service tetap jalan: `/health` menampilkan
`"ocr_ready": false` dan `/ocr-assist` otomatis memakai Gemini vision fallback.

## Menjalankan dengan Docker

```bash
docker build -t trustfund-ai .
docker run --rm -p 8000:8000 --env-file .env trustfund-ai
```

## Menjalankan test

Semua test memakai LLM/OCR yang di-mock (tanpa API asli, tanpa PaddleOCR):

```bash
pytest
```

## Benchmark INAPROC (perbandingan harga RAB)

Untuk menilai kewajaran harga, service membandingkan harga satuan tiap item RAB
dengan harga di **e-katalog INAPROC/LKPP**. Sumbernya adalah scraper terpisah
`inaproc-api` (ada di repo backend NexTrust) yang mengekspos
`GET /api/produk?keyword=<kata>`.

**Katalog INAPROC harus dipicu per keyword** — untuk mendapat data semen, harus
mencari keyword `semen` dulu. Karena nama item RAB sering terlalu spesifik
(mis. *"Semen Portland 40kg Tiga Roda"*), service menurunkan keyword pencarian
generik lebih dulu:

1. **Ekstraksi keyword** — Gemini memetakan tiap nama item → keyword singkat
   tanpa merek/angka/satuan (mis. → `semen portland`). Bila panggilan LLM gagal,
   dipakai heuristik lokal (buang angka+satuan, ambil 1-2 kata bermakna).
2. **Lookup** — service memanggil `inaproc-api` per keyword (di-cache agar
   keyword yang sama tidak dicari dua kali), mengambil daftar produk.
3. **Agregasi** — harga acuan = **median** `harga` produk yang ditemukan,
   berikut rentang min–max dan jumlah sampel. Bila sampel < `INAPROC_MIN_SAMPLES`,
   benchmark diabaikan (dianggap tidak cukup andal).
4. **Penilaian** — median + rentang dimasukkan ke prompt sebagai acuan utama;
   harga satuan jauh di atas rentang → sinyal mark-up.

Benchmark bersifat **best-effort**: bila `inaproc-api` mati/timeout/tak cukup
sampel, `benchmark_price` menjadi `null` dan LLM menilai berbasis pengetahuan
umum (confidence `RENDAH`) — validasi tidak pernah gagal karena benchmark.

Aktifkan dengan `INAPROC_ENABLED=true` dan arahkan `INAPROC_API_URL` ke scraper
yang berjalan. Default nonaktif (memakai `StubBenchmark` → selalu `null`).

## Endpoint

### POST /evaluate-rab (kontrak backend NexTrust)

Endpoint yang dipanggil `rabService.evaluateWithAI` di backend. Bentuk item
`{name, qty, unitPrice}` dan respons `{score, reasonable, notes}` sesuai kolom
tabel `RabCheck`. Field kampanye opsional (`campaignType`, `campaignTitle`,
`campaignDescription`, `location`) — bila backend mengirimnya, penilaian
relevansi item jadi lebih akurat.

```bash
curl -X POST http://localhost:8000/evaluate-rab \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" \
  -d '{
    "items": [
      {"name": "Semen Portland 40kg Tiga Roda", "qty": 50, "unitPrice": 65000},
      {"name": "Pasir cor", "qty": 10, "unitPrice": 300000}
    ],
    "total": 6250000,
    "targetAmount": 10000000
  }'
```

Respons (field tambahan di luar `score/reasonable/notes` diabaikan backend):

```json
{
  "score": 82,
  "reasonable": true,
  "notes": "Harga item sesuai kisaran e-katalog. ...",
  "verdict": "WAJAR",
  "total": 6250000,
  "benchmark_source": "INAPROC",
  "item_assessments": [ ... ],
  "flags": []
}
```

`reasonable = score >= 60` (konsisten dengan ambang fallback lama di backend).
`benchmark_source` = `INAPROC` bila minimal satu item dapat benchmark, selain
itu `LLM_KNOWLEDGE`.

### POST /api/v1/validate-rab

Versi kaya dari evaluasi RAB (dipakai internal / debugging). Menilai kewajaran
harga tiap item dan kelogisan item terhadap tujuan proyek. Verdict dihitung di
kode: skor >= 80 → `WAJAR`, 50-79 → `PERLU_REVIEW`, < 50 → `MENCURIGAKAN`.

```bash
curl -X POST http://localhost:8000/api/v1/validate-rab \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" \
  -d '{
    "campaign_id": "camp-123",
    "campaign_type": "PEMBANGUNAN",
    "campaign_title": "Pembangunan Sumur Bor Desa Cikembang",
    "campaign_description": "Membangun sumur bor untuk akses air bersih 50 KK.",
    "location": "Bandung, Jawa Barat",
    "items": [
      {"id": "item-1", "name": "Semen Portland 40kg", "quantity": 50, "unit": "sak", "unit_price": 65000, "subtotal": 3250000},
      {"id": "item-2", "name": "Pompa Air Submersible", "quantity": 1, "unit": "unit", "unit_price": 4500000, "subtotal": 4500000}
    ]
  }'
```

### POST /api/v1/ocr-assist

Best-effort: dari foto nota, hasilkan **saran** isian (item, qty, harga) untuk
dikoreksi manusia di frontend. Tidak menilai kewajaran; nilai yang tidak yakin
= `null` + confidence `RENDAH`. Selalu 200 dengan hasil parsial bila mungkin;
400 hanya untuk input yang bukan gambar.

Upload multipart (file + hint opsional):

```bash
curl -X POST http://localhost:8000/api/v1/ocr-assist \
  -H "X-Internal-Token: $INTERNAL_TOKEN" \
  -F "file=@nota.jpg" \
  -F 'hint_items=["Semen Portland 40kg","Pasir cor"]'
```

Atau JSON base64:

```bash
curl -X POST http://localhost:8000/api/v1/ocr-assist \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" \
  -d "{\"image_base64\": \"$(base64 -w0 nota.jpg)\", \"hint_items\": [\"Semen Portland 40kg\"]}"
```

Contoh respons:

```json
{
  "source": "paddleocr",
  "raw_text": "TOKO BANGUNAN JAYA\nSemen 40kg 50 x 65.000 = 3.250.000\n...",
  "suggested_items": [
    {"name": "Semen 40kg", "quantity": 50, "unit_price": 65000, "subtotal": 3250000, "confidence": "TINGGI"},
    {"name": "Paku", "quantity": null, "unit_price": null, "subtotal": 45000, "confidence": "RENDAH"}
  ],
  "detected_total": 3295000,
  "warnings": ["tulisan sebagian tidak terbaca"]
}
```

`source` = `paddleocr` (jalur utama) atau `vision_fallback` (PaddleOCR
gagal/kosong → gambar dikirim langsung ke Gemini multimodal; aktif via
`OCR_VISION_FALLBACK=true`).

### POST /api/v1/validate-milestone

Menilai bukti realisasi milestone. Input = **data yang sudah dikonfirmasi
yayasan** (hasil koreksi form), BUKAN hasil OCR mentah. Validasi 4 lapis:

1. **Kecocokan isi** — matching semantik item nota vs RAB milestone (LLM;
   "Semen Tiga Roda 50kg" cocok dengan "Semen 50kg").
2. **Kelengkapan & jumlah** — toleransi selisih nominal di kode: <= 10% OK,
   10-25% `REVIEW`, > 25% `SUSPICIOUS`; realisasi lebih murah lebih ditoleransi
   (threshold x `UNDERSPEND_TOLERANCE_MULTIPLIER`); item besar (> 30% nilai
   milestone) hilang → minimal PERLU_REVIEW; item kecil hilang (total <= 15%)
   → dicatat saja.
3. **Kewajaran** — harga realisasi dinilai LLM (benchmark stub seperti
   Validator RAB).
4. **Sinyal forensik** — ELA + metadata lokasi/waktu. AI TIDAK memvonis
   keaslian gambar; hanya menghasilkan SINYAL untuk review Dinsos.

Verdict di kode: sinyal ELA `TINGGI`, lokasi tidak terverifikasi, selisih >
25%, atau item besar hilang → **tidak pernah LOLOS otomatis**. Skor >= 80
tanpa flag berat → `LOLOS`; 50-79 atau ada flag → `PERLU_REVIEW`; < 50 →
`DITOLAK`.

Contoh kasus **LOLOS** (nota cocok, lokasi terverifikasi):

```bash
curl -X POST http://localhost:8000/api/v1/validate-milestone \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" \
  -d '{
    "campaign_id": "camp-123",
    "milestone_id": "ms-1",
    "campaign_type": "PEMBANGUNAN",
    "milestone_target_amount": 10000000,
    "planned_items": [
      {"id": "p1", "name": "Semen 50kg", "quantity": 100, "unit": "sak", "unit_price": 70000, "subtotal": 7000000},
      {"id": "p2", "name": "Pasir", "quantity": 10, "unit": "m3", "unit_price": 300000, "subtotal": 3000000}
    ],
    "confirmed_items": [
      {"name": "Semen Tiga Roda 50kg", "quantity": 100, "unit_price": 69000, "subtotal": 6900000},
      {"name": "Pasir cor", "quantity": 10, "unit_price": 310000, "subtotal": 3100000}
    ],
    "evidence_meta": {
      "photo_count": 3,
      "location_verified": true,
      "distance_from_project_m": 45,
      "captured_at": "2026-07-01T10:00:00Z",
      "ela_signals": [{"photo_ref": "nota1.jpg", "suspicion": "RENDAH"}]
    }
  }'
```

Contoh kasus **PERLU_REVIEW** (selisih 18% + lokasi tak terverifikasi):

```bash
curl -X POST http://localhost:8000/api/v1/validate-milestone \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" \
  -d '{
    "campaign_id": "camp-123",
    "milestone_id": "ms-1",
    "campaign_type": "PEMBANGUNAN",
    "milestone_target_amount": 10000000,
    "planned_items": [
      {"id": "p1", "name": "Semen 50kg", "quantity": 100, "unit": "sak", "unit_price": 70000, "subtotal": 7000000},
      {"id": "p2", "name": "Pasir", "quantity": 10, "unit": "m3", "unit_price": 300000, "subtotal": 3000000}
    ],
    "confirmed_items": [
      {"name": "Semen Tiga Roda 50kg", "quantity": 100, "unit_price": 82600, "subtotal": 8260000},
      {"name": "Pasir cor", "quantity": 10, "unit_price": 354000, "subtotal": 3540000}
    ],
    "evidence_meta": {
      "photo_count": 2,
      "location_verified": false,
      "distance_from_project_m": null,
      "captured_at": "2026-07-01T10:00:00Z",
      "ela_signals": []
    }
  }'
```

Bentuk respons:

```json
{
  "campaign_id": "camp-123",
  "milestone_id": "ms-1",
  "overall_score": 68,
  "verdict": "PERLU_REVIEW",
  "summary": "Item realisasi cocok dengan RAB, namun total realisasi 18% di atas target dan lokasi upload tidak terverifikasi.",
  "amount_check": {"target": 10000000, "declared": 11800000, "diff_pct": 18.0, "status": "REVIEW"},
  "matching": [
    {"planned_name": "Semen 50kg", "matched": "YA", "note": "Cocok dengan Semen Tiga Roda 50kg"},
    {"planned_name": "Pasir", "matched": "YA", "note": "Cocok dengan Pasir cor"}
  ],
  "forensic_summary": {"location": "FLAG", "image": "OK", "note": "Lokasi tidak terverifikasi saat upload bukti."},
  "flags": [
    "Selisih nominal +18.0% dari target milestone (perlu review).",
    "Lokasi upload bukti tidak terverifikasi."
  ]
}
```

### POST /api/v1/forensic-ela (opsional)

Hitung sinyal ELA satu foto (multipart `file` atau JSON `image_base64`).
Backend Node.js memakai hasilnya untuk mengisi `evidence_meta.ela_signals`.

```bash
curl -X POST http://localhost:8000/api/v1/forensic-ela \
  -H "X-Internal-Token: $INTERNAL_TOKEN" \
  -F "file=@bukti1.jpg"
# → {"photo_ref": "bukti1.jpg", "suspicion": "RENDAH", "metrics": {"mean_diff": 3.2, "max_diff": 41.0, "jpeg_quality": 90.0}}
```

### GET /health

```json
{"status": "ok", "model": "gemini-2.5-flash", "benchmark_enabled": true, "benchmark_source": "INAPROC", "ocr_ready": false}
```

## Daftar ENV

| ENV                              | Default            | Keterangan                                                       |
|-----------------------------------|---------------------|--------------------------------------------------------------------|
| `GEMINI_API_KEY`                   | -                   | API key Google Gemini (wajib).                                     |
| `GEMINI_MODEL`                     | `gemini-2.5-flash`  | Nama model Gemini.                                                 |
| `INTERNAL_TOKEN`                   | -                   | Shared secret header `X-Internal-Token`.                            |
| `INTERNAL_AUTH_ENABLED`            | `true`              | `false` = tanpa cek token (dev saja).                               |
| `CORS_ORIGINS`                     | `*`                 | Origin diizinkan, dipisah koma.                                     |
| `INAPROC_ENABLED`                  | `false`             | `true` = pakai benchmark harga e-katalog INAPROC.                   |
| `INAPROC_API_URL`                  | `http://localhost:3000` | URL scraper `inaproc-api` yang berjalan.                        |
| `INAPROC_TIMEOUT_SECONDS`          | `20`                | Timeout tiap pencarian keyword ke INAPROC.                          |
| `INAPROC_PER_PAGE`                 | `30`                | Jumlah produk diambil per keyword.                                  |
| `INAPROC_MIN_SAMPLES`              | `3`                 | Minimal produk agar benchmark dianggap valid.                       |
| `OCR_VISION_FALLBACK`              | `true`              | Kirim gambar ke Gemini bila PaddleOCR gagal/kosong.                 |
| `OCR_LANG`                         | `latin`             | Bahasa model PaddleOCR (`latin` mencakup Indonesia).                |
| `OCR_MIN_TEXT_LEN`                 | `20`                | Teks OCR lebih pendek dari ini dianggap gagal → fallback.           |
| `DIFF_REVIEW_PCT`                  | `10`                | Selisih nominal <= ini masih toleransi.                             |
| `DIFF_SUSPICIOUS_PCT`              | `25`                | Selisih di atas ini → SUSPICIOUS.                                   |
| `UNDERSPEND_TOLERANCE_MULTIPLIER`  | `1.5`               | Pengali threshold bila realisasi lebih murah.                       |
| `MISSING_MAJOR_ITEM_PCT`           | `30`                | Item > % nilai milestone ini hilang → PERLU_REVIEW.                 |
| `MISSING_MINOR_TOTAL_PCT`          | `15`                | Total item hilang <= % ini → cukup dicatat.                          |
| `GEO_RADIUS_METERS`                | `200`               | Radius wajar upload bukti dari lokasi proyek.                       |
| `ELA_JPEG_QUALITY`                 | `90`                | Kualitas re-save JPEG untuk ELA.                                    |
| `ELA_SEDANG_THRESHOLD`             | `12`                | mean_diff >= ini → suspicion SEDANG.                                |
| `ELA_TINGGI_THRESHOLD`             | `20`                | mean_diff >= ini → suspicion TINGGI.                                |

## Catatan OCR (PaddleOCR ditunda)

Saat ini **PaddleOCR sengaja tidak dipasang** karena terlalu berat (unduhan
> 1 GB, RAM >= 2 GB). `/ocr-assist` tetap berfungsi penuh lewat **Gemini
vision fallback** (`OCR_VISION_FALLBACK=true`): gambar nota dikirim langsung
ke Gemini multimodal, dan `source` pada respons menjadi `vision_fallback`.
`/health` menampilkan `"ocr_ready": false`.

Kode sudah siap untuk PaddleOCR — engine dimuat **sekali** saat startup
(FastAPI lifespan) bila paket tersedia. Untuk mengaktifkannya nanti:

1. Uncomment `paddleocr` dan `paddlepaddle` di `requirements.txt`, lalu
   `pip install -r requirements.txt`.
2. Uncomment blok `apt-get install ... libgl1 libglib2.0-0 libgomp1` di
   `Dockerfile` (dependency sistem OpenCV/Paddle).

Tidak ada perubahan kode yang diperlukan: begitu PaddleOCR terpasang,
`/ocr-assist` otomatis memakainya sebagai jalur utama dan vision fallback
hanya dipakai bila PaddleOCR gagal/hasilnya kosong.

## Integrasi dengan backend Node.js (NexTrust)

Service ini tidak menyimpan apa pun. Backend Node.js yang menyimpan hasil ke
database/blockchain dan mengambil keputusan bisnis. Untuk milestone:

- `LOLOS` → dana milestone dicairkan.
- `PERLU_REVIEW` → masuk antrean review Dinsos.
- `DITOLAK` → minta yayasan memperbaiki/melengkapi bukti.

### Penyesuaian yang perlu dilakukan di backend NexTrust

`rabService.evaluateWithAI` sudah memanggil `POST {AI_SERVICE_URL}/evaluate-rab`
dengan bentuk `{items:[{name,qty,unitPrice}], total, targetAmount}` dan membaca
`{score, reasonable, notes}` — persis kontrak yang diekspos service ini. Dua hal
kecil yang perlu disetel:

1. **`AI_SERVICE_URL`** → arahkan ke service ini (mis. `http://trustfund-ai:8000`).
2. **Header auth** → tambahkan `X-Internal-Token` pada `fetch` di
   `evaluateWithAI` (saat ini belum dikirim). Bila `INTERNAL_AUTH_ENABLED=true`,
   tanpa header ini request ditolak `401`:

   ```js
   const res = await fetch(`${aiUrl}/evaluate-rab`, {
     method: "POST",
     headers: {
       "Content-Type": "application/json",
       "X-Internal-Token": process.env.AI_INTERNAL_TOKEN, // <— tambahkan
     },
     body: JSON.stringify({ items, total, targetAmount: target }),
   });
   ```

Nilai `score`, `reasonable`, `notes` langsung dipetakan ke kolom tabel
`RabCheck`. Enum kategori kampanye sudah selaras dengan `CampaignCategory`
(`ALAT_KESEHATAN`, bukan `ALAT_BANTU`).

> **Benchmark INAPROC:** agar `/evaluate-rab` memakai harga e-katalog sebagai
> acuan, jalankan scraper `inaproc-api` (di repo backend) lalu set
> `INAPROC_ENABLED=true` + `INAPROC_API_URL` di service ini. Tanpa itu,
> penilaian tetap jalan berbasis pengetahuan LLM (`benchmark_price: null`).

### Contoh pemanggilan lain (axios)

```js
const axios = require("axios");
const FormData = require("form-data");
const fs = require("fs");

const AI = axios.create({
  baseURL: process.env.AI_SERVICE_URL, // http://trustfund-ai:8000
  headers: { "X-Internal-Token": process.env.AI_INTERNAL_TOKEN },
});

// 1) OCR assist: kirim foto nota, terima saran isian untuk form
async function ocrAssist(filePath, hintItems) {
  const form = new FormData();
  form.append("file", fs.createReadStream(filePath));
  form.append("hint_items", JSON.stringify(hintItems));
  const { data } = await AI.post("/api/v1/ocr-assist", form, { headers: form.getHeaders(AI.defaults.headers) });
  // tampilkan data.suggested_items di form frontend untuk DIKOREKSI yayasan;
  // nilai null / confidence RENDAH wajib diisi manual
  return data;
}

// 2) Validasi milestone: kirim data TERKONFIRMASI yayasan (bukan OCR mentah)
async function validateMilestone(payload) {
  const { data } = await AI.post("/api/v1/validate-milestone", payload);
  switch (data.verdict) {
    case "LOLOS":        /* cairkan dana milestone */ break;
    case "PERLU_REVIEW": /* antrekan ke review Dinsos */ break;
    case "DITOLAK":      /* minta yayasan perbaiki bukti */ break;
  }
  return data;
}
```

Jika Gemini gagal/melenceng dari schema, endpoint validasi mengembalikan HTTP
`502` `{"error": "llm_validation_failed", "detail": "..."}` — tangani sebagai
kegagalan sementara (retry / tandai review manual), bukan penolakan kampanye.
`/ocr-assist` tidak pernah 5xx untuk kegagalan OCR/LLM: ia mengembalikan 200
dengan `suggested_items` kosong + `warnings`, karena sifatnya best-effort.
