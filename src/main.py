from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.api.routes import router
from src.config import get_settings
from src.db.session import dispose_database
from src.integrations.langfuse import configure_langfuse, flush_langfuse, tracing_enabled
from src.models.schemas import ErrorResponse, ReadinessResponse
from src.services.chat import get_runtime

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.validate_chunk_settings()
    configure_langfuse()
    tracing = "enabled" if tracing_enabled() else "disabled"
    print(f"Starting {settings.app_name} in {settings.app_env} mode (langfuse {tracing})")
    yield
    flush_langfuse()
    await get_runtime().close()
    await dispose_database()
    print("Shutting down...")


app = FastAPI(
    title="MediPay Agent API",
    description=(
        "Backend API for BHYT questions, hospital fee analysis, and payment guidance. "
        "Built with FastAPI and grounded PostgreSQL/pgvector + Neo4j GraphRAG."
    ),
    version="1.0.0",
    lifespan=lifespan,
    openapi_tags=[
        {"name": "Agent", "description": "Grounded chat and agent status endpoints."},
        {"name": "System", "description": "Application health and readiness endpoints."},
    ],
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Request-ID"],
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    supplied = request.headers.get("X-Request-ID", "").strip()
    request_id = supplied[:128] if supplied else uuid.uuid4().hex
    request.state.request_id = request_id
    try:
        response = await call_next(request)
    except Exception as error:
        logger.exception("Unhandled middleware error", exc_info=error)
        response = _error_response(request, 500, "internal_error", "An unexpected error occurred")
    response.headers["X-Request-ID"] = request_id
    return response


def _error_response(request: Request, status_code: int, code: str, message: str) -> JSONResponse:
    body = ErrorResponse(
        code=code,
        message=message,
        request_id=getattr(request.state, "request_id", "unknown"),
    )
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, error: RequestValidationError):
    logger.info("Invalid request", extra={"request_id": request.state.request_id, "errors": error.errors()})
    return _error_response(request, 422, "invalid_request", "Request validation failed")


@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, error: HTTPException):
    code = {
        502: "provider_unavailable",
        503: "dependency_unavailable",
    }.get(error.status_code, "internal_error")
    return _error_response(request, error.status_code, code, str(error.detail))


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, error: Exception):
    logger.exception("Unhandled API error", extra={"request_id": request.state.request_id})
    return _error_response(request, 500, "internal_error", "An unexpected error occurred")


app.include_router(router, prefix="/api/v1")


@app.get(
    "/health",
    tags=["System"],
    summary="Check API liveness",
    description="Return immediately when the API process is running.",
)
async def health():
    return {"status": "ok", "env": settings.app_env}


@app.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse}},
    tags=["System"],
    summary="Check GraphRAG readiness",
    description="Check OpenAI, embedding, PostgreSQL, and Neo4j dependencies.",
)
async def readiness() -> ReadinessResponse | JSONResponse:
    checks = await get_runtime().readiness()
    ready = all(checks.values())
    response = ReadinessResponse(
        status="ready" if ready else "degraded",
        **checks,
        details={"required": [name for name, ok in checks.items() if not ok]},
    )
    if not ready:
        return JSONResponse(status_code=503, content=response.model_dump(mode="json"))
    return response
