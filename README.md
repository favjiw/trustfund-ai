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
| `POST /api/v1/plan-milestones`    | Wajib, menentukan     | **AI Planner** — susun draf struktur milestone dari RAB (retensi progresif) |
| `POST /api/v1/validate-milestone-structure` | Wajib, deterministik | Pagar struktur milestone (tanpa LLM) — dipanggil tiap yayasan edit draf |
| `POST /api/v1/parse-nota`         | Opsional, asistensi   | Foto nota → JSON item terstruktur via vision LLM (dikoreksi manusia) |
| `POST /api/v1/validate-milestone` | Wajib, menentukan     | Menilai bukti realisasi milestone yang sudah dikonfirmasi yayasan |
| `POST /api/v1/forensic-ela`       | Opsional              | Menghitung sinyal ELA satu foto (pendukung validate-milestone) |

Penilaian harga RAB memakai **benchmark e-katalog INAPROC** sebagai acuan bila
diaktifkan (lihat bagian *Benchmark INAPROC* di bawah). Enum kategori kampanye
selaras dengan `CampaignCategory` di skema Prisma backend: `PEMBANGUNAN`,
`PENGADAAN_BARANG`, `ALAT_KESEHATAN`, `REKONSTRUKSI`.

> **Penting — endpoint internal.** Semua endpoint hanya boleh dipanggil oleh
> backend Node.js TrustFund dengan header `X-Internal-Token` yang cocok dengan
> ENV `INTERNAL_TOKEN`. Jangan expose service ini langsung ke internet.

> **Prinsip inti parse nota:** hasil baca nota BUKAN input utama.
> `/parse-nota` (vision LLM) hanya memberi SARAN isian; yayasan
> mengoreksi/mengonfirmasi di web; `/validate-milestone` (DeepSeek) bekerja
> atas data TERKONFIRMASI yayasan, BUKAN hasil baca mentah.
>
> Alur nota: **upload nota → `/parse-nota` (vision LLM, JSON konsisten) →
> konfirmasi yayasan → `/validate-milestone` (DeepSeek mencocokkan dengan RAB
> milestone terkunci).**

## Struktur proyek

```
app/
  main.py                        # FastAPI app, routes, CORS, auth, error handler
  config.py                      # baca ENV (pydantic-settings)
  models.py                      # semua Pydantic schema (request/response)
  services/
    llm_client.py                 # wrapper LLM OpenAI-compatible: teks → model utama, gambar → model vision
    benchmark.py                  # PriceBenchmark (abstrak) + StubBenchmark (MVP, selalu None)
    validator.py                  # Validator RAB: benchmark → prompt → LLM → skor→verdict
    planner.py                    # AI Planner: LLM susun draf milestone, kode tegakkan pagar
    structure_guard.py            # Pagar struktur milestone (deterministik, tanpa LLM)
    nota_parser.py                # Foto nota → JSON item terstruktur (vision LLM)
    forensic.py                   # ELA (Error Level Analysis) deterministik
    milestone_validator.py         # Validator bukti milestone: 4 lapis + verdict di kode
  prompts/
    rab_prompt.py                  # prompt Validator RAB
    planner_prompt.py              # prompt AI Planner (pagar ditulis eksplisit)
    nota_prompt.py                 # prompt baca nota (vision)
    milestone_prompt.py            # prompt matching semantik + kewajaran realisasi
tests/                           # semua test dengan LLM di-mock
```

## Menjalankan secara lokal

```bash
python -m venv .venv
source .venv/Scripts/activate     # Git Bash; PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env              # isi LLM_API_KEY Sumopod, INTERNAL_TOKEN, LLM_VISION_MODEL
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

`LLM_VISION_MODEL` (mis. `gpt-5-nano` di Sumopod) wajib diisi agar `/parse-nota`
berfungsi — model utama DeepSeek text-only. Tanpa itu, semua endpoint lain
tetap jalan; hanya `/parse-nota` yang mengembalikan 502.

## Menjalankan dengan Docker

```bash
docker build -t trustfund-ai .
docker run --rm -p 8000:8000 --env-file .env trustfund-ai
```

## Menjalankan test

Semua test memakai LLM yang di-mock (tanpa API asli):

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

1. **Ekstraksi keyword** — LLM memetakan tiap nama item → keyword singkat
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

### POST /api/v1/parse-nota

Foto nota → JSON item terstruktur via **vision LLM** (`LLM_VISION_MODEL`).
Tidak menilai kewajaran; nilai yang tidak yakin = `null` + confidence `RENDAH`.
Hasilnya ditampilkan di form frontend untuk DIKONFIRMASI yayasan, lalu backend
mengirim data terkonfirmasi ke `/validate-milestone`. Gagal baca (model/jaringan)
= HTTP 502 eksplisit; 400 untuk input yang bukan gambar.

Upload multipart (file + hint opsional):

```bash
curl -X POST http://localhost:8000/api/v1/parse-nota \
  -H "X-Internal-Token: $INTERNAL_TOKEN" \
  -F "file=@nota.jpg" \
  -F 'hint_items=["Semen Portland 40kg","Pasir cor"]'
```

Atau JSON base64:

```bash
curl -X POST http://localhost:8000/api/v1/parse-nota \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" \
  -d "{\"image_base64\": \"$(base64 -w0 nota.jpg)\", \"hint_items\": [\"Semen Portland 40kg\"]}"
```

Contoh respons:

```json
{
  "raw_text": "TOKO BANGUNAN JAYA\nSemen 40kg 50 x 65.000 = 3.250.000\n...",
  "items": [
    {"name": "Semen 40kg", "quantity": 50, "unit_price": 65000, "subtotal": 3250000, "confidence": "TINGGI"},
    {"name": "Paku", "quantity": null, "unit_price": null, "subtotal": 45000, "confidence": "RENDAH"}
  ],
  "detected_total": 3295000,
  "warnings": ["tulisan sebagian tidak terbaca"]
}
```

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
{"status": "ok", "model": "deepseek-v4-pro", "vision_model": "gpt-5-nano", "vision_ready": true, "benchmark_enabled": true, "benchmark_source": "INAPROC"}
```

## Daftar ENV

| ENV                              | Default            | Keterangan                                                       |
|-----------------------------------|---------------------|--------------------------------------------------------------------|
| `LLM_API_KEY`                      | -                   | API key LLM OpenAI-compatible / Sumopod (wajib).                   |
| `LLM_MODEL`                        | `deepseek-v4-pro`   | Nama model (mis. `deepseek-v4-pro`).                                |
| `LLM_BASE_URL`                     | `https://ai.sumopod.com/v1` | Base URL API (endpoint `/chat/completions`).               |
| `LLM_TIMEOUT_SECONDS`              | `60`                | Timeout tiap panggilan LLM.                                        |
| `LLM_MAX_TOKENS`                   | `4096`              | Batas token output.                                                |
| `LLM_SUPPORTS_VISION`             | `false`             | `true` bila model UTAMA mendukung input gambar (DeepSeek: teks saja).|
| `LLM_VISION_MODEL`                 | (kosong)            | Model vision untuk `/parse-nota` (mis. `gpt-5-nano`). Wajib bila model utama text-only.|
| `LLM_VISION_BASE_URL`              | (kosong)            | Endpoint model vision bila beda provider; kosong = ikut `LLM_BASE_URL`.|
| `LLM_VISION_API_KEY`               | (kosong)            | API key model vision bila beda provider; kosong = ikut `LLM_API_KEY`.|
| `MILESTONE_MIN_COUNT` / `MILESTONE_MAX_COUNT` | `2` / `6` | Batas keras jumlah tahap milestone.                             |
| `DP_MAX_PCT`                       | `15`                | Porsi maksimal milestone pertama (DP).                              |
| `MILESTONE_MAX_PCT`                | `40`                | Porsi maksimal satu milestone.                                      |
| `FINAL_RETENTION_MIN_PCT`          | `20`                | Retensi akhir di bawah ini → warning.                               |
| `PLANNER_MAX_ATTEMPTS`             | `2`                 | Percobaan AI Planner sebelum menyerah (retry dengan feedback pagar).|
| `INTERNAL_TOKEN`                   | -                   | Shared secret header `X-Internal-Token`.                            |
| `INTERNAL_AUTH_ENABLED`            | `true`              | `false` = tanpa cek token (dev saja).                               |
| `CORS_ORIGINS`                     | `*`                 | Origin diizinkan, dipisah koma.                                     |
| `INAPROC_ENABLED`                  | `false`             | `true` = pakai benchmark harga e-katalog INAPROC.                   |
| `INAPROC_API_URL`                  | `http://localhost:3000` | URL scraper `inaproc-api` yang berjalan.                        |
| `INAPROC_TIMEOUT_SECONDS`          | `20`                | Timeout tiap pencarian keyword ke INAPROC.                          |
| `INAPROC_PER_PAGE`                 | `30`                | Jumlah produk diambil per keyword.                                  |
| `INAPROC_MIN_SAMPLES`              | `3`                 | Minimal produk agar benchmark dianggap valid.                       |
| `DIFF_REVIEW_PCT`                  | `10`                | Selisih nominal <= ini masih toleransi.                             |
| `DIFF_SUSPICIOUS_PCT`              | `25`                | Selisih di atas ini → SUSPICIOUS.                                   |
| `UNDERSPEND_TOLERANCE_MULTIPLIER`  | `1.5`               | Pengali threshold bila realisasi lebih murah.                       |
| `MISSING_MAJOR_ITEM_PCT`           | `30`                | Item > % nilai milestone ini hilang → PERLU_REVIEW.                 |
| `MISSING_MINOR_TOTAL_PCT`          | `15`                | Total item hilang <= % ini → cukup dicatat.                          |
| `GEO_RADIUS_METERS`                | `200`               | Radius wajar upload bukti dari lokasi proyek.                       |
| `ELA_JPEG_QUALITY`                 | `90`                | Kualitas re-save JPEG untuk ELA.                                    |
| `ELA_SEDANG_THRESHOLD`             | `12`                | mean_diff >= ini → suspicion SEDANG.                                |
| `ELA_TINGGI_THRESHOLD`             | `20`                | mean_diff >= ini → suspicion TINGGI.                                |

## Catatan model vision (parse nota)

Model utama (`deepseek-v4-pro` via Sumopod) **text-only** — terverifikasi:
gateway membuang bagian gambar (prompt_tokens tidak bertambah) dan model
menjawab tidak melihat gambar. Karena itu `/parse-nota` memakai model vision
terpisah (`LLM_VISION_MODEL`, mis. `gpt-5-nano` di Sumopod yang sama — murah,
±$0,002/nota). Request bergambar juga sengaja **tanpa** `temperature` dan
`max_tokens` karena model reasoning (gpt-5-*) menolak temperature non-default
dan bisa menghabiskan budget token untuk reasoning hingga JSON terpotong.

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

// 1) Parse nota: kirim foto nota, terima JSON item untuk form
async function parseNota(filePath, hintItems) {
  const form = new FormData();
  form.append("file", fs.createReadStream(filePath));
  form.append("hint_items", JSON.stringify(hintItems));
  const { data } = await AI.post("/api/v1/parse-nota", form, { headers: form.getHeaders(AI.defaults.headers) });
  // tampilkan data.items di form frontend untuk DIKONFIRMASI yayasan;
  // nilai null / confidence RENDAH wajib diisi manual
  return data;
}

// 2) Validasi milestone: kirim data TERKONFIRMASI yayasan (bukan hasil baca mentah)
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

Jika LLM gagal/melenceng dari schema, endpoint mengembalikan HTTP `502`
(`llm_validation_failed` / `llm_planning_failed` / `llm_nota_failed`) — tangani
sebagai kegagalan sementara (retry / tandai review manual), bukan penolakan.
`/parse-nota` juga 502 bila gagal baca — JANGAN ditafsirkan sebagai "nota tidak
valid"; biarkan yayasan mengisi form manual sebagai fallback.
