import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.models import ValidateRABRequest, ValidateRABResponse
from app.services.benchmark import StubBenchmark
from app.services.llm_client import GeminiClient
from app.services.validator import RABValidationError, RABValidator

logger = logging.getLogger("trustfund_ai")

PUBLIC_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    llm_client = GeminiClient(api_key=settings.gemini_api_key, model=settings.gemini_model)
    benchmark = StubBenchmark()
    validator = RABValidator(llm_client=llm_client, benchmark=benchmark)

    app.state.settings = settings
    app.state.validator = validator

    yield


app = FastAPI(
    title="TrustFund AI - RAB Validator",
    description=(
        "Microservice AI untuk memvalidasi kewajaran RAB (Rencana Anggaran Biaya) "
        "kampanye donasi TrustFund. Endpoint ini bersifat internal, hanya untuk "
        "dipanggil oleh backend Node.js."
    ),
    version="1.0.0",
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


@app.exception_handler(RABValidationError)
async def rab_validation_error_handler(request: Request, exc: RABValidationError):
    logger.error("RAB validation failed: %s", exc)
    return JSONResponse(
        status_code=502,
        content={"error": "llm_validation_failed", "detail": str(exc)},
    )


@app.get("/health")
async def health(request: Request):
    settings = request.app.state.settings
    return {
        "status": "ok",
        "model": settings.gemini_model,
        "benchmark_enabled": False,
    }


@app.post("/api/v1/validate-rab", response_model=ValidateRABResponse)
async def validate_rab(payload: ValidateRABRequest, request: Request):
    validator: RABValidator = request.app.state.validator
    return validator.validate(payload)
