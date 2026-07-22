import base64
import binascii
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.models import (
    ElaResponse,
    EvaluateRABRequest,
    EvaluateRABResponse,
    OcrAssistResponse,
    PlanMilestonesRequest,
    PlanMilestonesResponse,
    StructureCheckResult,
    ValidateMilestoneRequest,
    ValidateMilestoneResponse,
    ValidateRABRequest,
    ValidateRABResponse,
    ValidateStructureRequest,
)
from app.services.benchmark import build_benchmark
from app.services.forensic import InvalidForensicImageError, compute_ela
from app.services.llm_client import LLMClient
from app.services.milestone_validator import MilestoneValidationError, MilestoneValidator
from app.services.ocr_service import InvalidImageError, OcrService
from app.services.planner import MilestonePlanner, PlannerError
from app.services.structure_guard import check_structure
from app.services.validator import RABValidationError, RABValidator

logger = logging.getLogger("trustfund_ai")

PUBLIC_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}


def _validate_settings(settings) -> None:
    """Fail-fast: pastikan konfigurasi kritikal terisi sebelum melayani request."""
    if not settings.llm_api_key.strip():
        raise RuntimeError("LLM_API_KEY belum diisi — set di .env sebelum menjalankan service.")

    if settings.internal_auth_enabled:
        if not settings.internal_token.strip():
            raise RuntimeError("INTERNAL_AUTH_ENABLED=true tetapi INTERNAL_TOKEN kosong.")
    else:
        logger.warning(
            "INTERNAL_AUTH_ENABLED=false — endpoint TANPA proteksi token. "
            "Hanya untuk dev lokal; JANGAN dipakai di production."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    _validate_settings(settings)

    llm_client = LLMClient(
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        timeout_seconds=settings.llm_timeout_seconds,
        max_tokens=settings.llm_max_tokens,
        supports_vision=settings.llm_supports_vision,
        max_retries=settings.llm_max_retries,
        retry_backoff_seconds=settings.llm_retry_backoff_seconds,
    )
    benchmark = build_benchmark(settings)
    validator = RABValidator(llm_client=llm_client, benchmark=benchmark)

    ocr_service = OcrService(settings=settings, llm_client=llm_client)
    ocr_service.load()  # muat model PaddleOCR sekali di sini, bukan per-request

    milestone_validator = MilestoneValidator(llm_client=llm_client, settings=settings)
    planner = MilestonePlanner(llm_client=llm_client, settings=settings)

    app.state.settings = settings
    app.state.validator = validator
    app.state.ocr_service = ocr_service
    app.state.milestone_validator = milestone_validator
    app.state.planner = planner

    yield


app = FastAPI(
    root_path="/trustfund-ai",
    title="TrustFund AI - RAB Validator",
    description=(
        "Microservice AI untuk memvalidasi kewajaran RAB, OCR assist nota, dan "
        "validasi bukti milestone kampanye donasi TrustFund. Endpoint ini bersifat "
        "internal, hanya untuk dipanggil oleh backend Node.js."
    ),
    version="2.0.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def internal_token_guard(request: Request, call_next):
    settings = request.app.state.settings if hasattr(request.app.state, "settings") else get_settings()

    if not settings.internal_auth_enabled or request.url.path in PUBLIC_PATHS:
        return await call_next(request)

    token = request.headers.get("X-Internal-Token")
    if token != settings.internal_token:
        return JSONResponse(status_code=401, content={"error": "unauthorized", "detail": "X-Internal-Token tidak valid"})

    return await call_next(request)


# Ditambahkan setelah token guard agar CORS membungkus di luar (menangani
# preflight OPTIONS sebelum request sempat kena cek token).
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Pesan generik ke klien; detail lengkap (mungkin memuat respons provider) hanya di log server.
_LLM_ERROR_DETAIL = "Evaluasi AI gagal sementara. Silakan coba lagi."


@app.exception_handler(RABValidationError)
async def rab_validation_error_handler(request: Request, exc: RABValidationError):
    logger.error("RAB validation failed: %s", exc)
    return JSONResponse(
        status_code=502,
        content={"error": "llm_validation_failed", "detail": _LLM_ERROR_DETAIL},
    )


@app.exception_handler(PlannerError)
async def planner_error_handler(request: Request, exc: PlannerError):
    logger.error("Milestone planning failed: %s", exc)
    return JSONResponse(
        status_code=502,
        content={"error": "llm_planning_failed", "detail": _LLM_ERROR_DETAIL},
    )


@app.exception_handler(MilestoneValidationError)
async def milestone_validation_error_handler(request: Request, exc: MilestoneValidationError):
    logger.error("Milestone validation failed: %s", exc)
    return JSONResponse(
        status_code=502,
        content={"error": "llm_validation_failed", "detail": _LLM_ERROR_DETAIL},
    )


@app.get("/health")
async def health(request: Request):
    settings = request.app.state.settings
    ocr_service: OcrService = request.app.state.ocr_service
    return {
        "status": "ok",
        "model": settings.llm_model,
        "benchmark_enabled": settings.inaproc_enabled,
        "benchmark_source": "INAPROC" if settings.inaproc_enabled else None,
        "ocr_ready": ocr_service.ocr_ready,
    }


@app.post("/api/v1/validate-rab", response_model=ValidateRABResponse)
async def validate_rab(payload: ValidateRABRequest, request: Request):
    validator: RABValidator = request.app.state.validator
    return validator.validate(payload)


@app.post("/evaluate-rab", response_model=EvaluateRABResponse)
async def evaluate_rab(payload: EvaluateRABRequest, request: Request):
    """Kontrak yang dipanggil backend NexTrust (rabService.evaluateWithAI):
    {items:[{name,qty,unitPrice}], total, targetAmount} → {score, reasonable, notes}."""
    validator: RABValidator = request.app.state.validator
    return validator.evaluate(payload)


def _parse_hint_items(raw: str | None) -> list[str]:
    if not raw or not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
        return [str(h) for h in parsed] if isinstance(parsed, list) else [str(parsed)]
    except json.JSONDecodeError:
        return [h.strip() for h in raw.split(",") if h.strip()]


async def _image_from_json(request: Request) -> tuple[bytes, dict]:
    """Fallback JSON base64 saat tidak ada file yang diunggah (mis. dari backend)."""
    try:
        body = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Kirim file gambar (multipart) atau JSON {\"image_base64\": ...}")

    image_base64 = body.get("image_base64") if isinstance(body, dict) else None
    if not image_base64:
        raise HTTPException(status_code=400, detail="Field 'image_base64' wajib ada di body JSON")

    try:
        return base64.b64decode(image_base64, validate=True), body
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="'image_base64' bukan base64 yang valid")


@app.post("/api/v1/ocr-assist", response_model=OcrAssistResponse)
async def ocr_assist(
    request: Request,
    file: UploadFile | None = File(default=None, description="Foto nota (JPEG/PNG)"),
    hint_items: str | None = Form(default=None, description="Opsional: JSON array atau CSV nama item RAB terkait"),
):
    """Terima foto nota via upload file (multipart) ATAU JSON {image_base64}."""
    ocr_service: OcrService = request.app.state.ocr_service

    if file is not None:
        image_bytes = await file.read()
        hints = _parse_hint_items(hint_items)
    else:
        image_bytes, body = await _image_from_json(request)
        hints = [str(h) for h in (body.get("hint_items") or [])]

    try:
        return ocr_service.assist(image_bytes, hints)
    except InvalidImageError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/v1/plan-milestones", response_model=PlanMilestonesResponse)
async def plan_milestones(payload: PlanMilestonesRequest, request: Request):
    """AI Planner: susun draf struktur milestone (retensi progresif) dari RAB +
    konteks kampanye. Draf sudah lolos pagar sistem (lihat structure_check)."""
    planner: MilestonePlanner = request.app.state.planner
    return planner.plan(payload)


@app.post("/api/v1/validate-milestone-structure", response_model=StructureCheckResult)
async def validate_milestone_structure(payload: ValidateStructureRequest, request: Request):
    """Pagar struktur milestone — deterministik, tanpa LLM. Dipanggil backend tiap
    yayasan mengedit draf, sebelum struktur di-LOCK ke smart contract."""
    settings = request.app.state.settings
    return check_structure(payload.campaign_type, payload.milestones, settings)


@app.post("/api/v1/validate-milestone", response_model=ValidateMilestoneResponse)
async def validate_milestone(payload: ValidateMilestoneRequest, request: Request):
    milestone_validator: MilestoneValidator = request.app.state.milestone_validator
    return milestone_validator.validate(payload)


@app.post("/api/v1/forensic-ela", response_model=ElaResponse)
async def forensic_ela(
    request: Request,
    file: UploadFile | None = File(default=None, description="Foto bukti (JPEG/PNG)"),
):
    """Hitung sinyal ELA sebuah foto. Terima upload file (multipart) ATAU JSON
    {image_base64}. Hasilnya dipakai backend untuk mengisi evidence_meta.ela_signals."""
    settings = request.app.state.settings

    if file is not None:
        image_bytes = await file.read()
        photo_ref = file.filename
    else:
        image_bytes, body = await _image_from_json(request)
        photo_ref = body.get("photo_ref")

    try:
        suspicion, metrics = compute_ela(image_bytes, settings)
    except InvalidForensicImageError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return ElaResponse(photo_ref=photo_ref, suspicion=suspicion, metrics=metrics)
