from __future__ import annotations

import hashlib
import hmac
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

from src.api.auth_routes import router as auth_router
from src.api.limits import (
    CostQuota,
    InMemoryCostQuota,
    InMemoryRateLimiter,
    RateLimiter,
    RedisCostQuota,
    RedisRateLimiter,
)
from src.api.routes import router
from src.config import get_settings
from src.db.session import dispose_database
from src.integrations.langfuse import configure_langfuse, flush_langfuse, tracing_enabled
from src.models.schemas import ErrorResponse, ReadinessResponse
from src.services.chat import get_runtime
from src.services.llm import close_llm
from src.services.metrics import metrics

logger = logging.getLogger(__name__)
_rate_limiter: RateLimiter | None = None
_cost_quota: CostQuota | None = None


class _BodyLimitExceededError(Exception):
    """Internal signal raised by the ASGI receive guard for chunked bodies."""


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.validate_chunk_settings()
    settings.validate_production_contract()
    global _rate_limiter, _cost_quota
    _rate_limiter = _build_rate_limiter(settings)
    _cost_quota = _build_cost_quota(settings)
    configure_langfuse()
    tracing = "enabled" if tracing_enabled() else "disabled"
    print(f"Starting {settings.app_name} in {settings.app_env} mode (langfuse {tracing})")
    if settings.app_env != "test":
        await get_runtime().prewarm()
        # Populate the coalesced readiness cache before the first orchestrator
        # probe.  This keeps a cold burst of readiness requests from all
        # waiting on the same Qdrant/Neo4j count query while preserving the
        # degraded response contract when a dependency is unavailable.
        await get_runtime().readiness()
    yield
    flush_langfuse()
    await get_runtime().close()
    if _rate_limiter is not None:
        await _rate_limiter.close()
    if _cost_quota is not None:
        await _cost_quota.close()
    close_llm()
    await dispose_database()
    print("Shutting down...")


app = FastAPI(
    title="MediPay Agent API",
    description=(
        "Backend API for BHYT questions, hospital fee analysis, and payment guidance. "
        "Built with FastAPI, Supabase lexical/PageIndex, Qdrant semantic search, and Neo4j GraphRAG."
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
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Request-ID", "Authorization"],
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    global _rate_limiter, _cost_quota
    supplied = request.headers.get("X-Request-ID", "").strip()
    request_id = supplied[:128] if supplied else uuid.uuid4().hex
    request.state.request_id = request_id
    started = time.perf_counter()
    try:
        if request.method == "POST" and request.url.path in {
            "/api/v1/chat", "/api/v1/chat/stream", "/api/v1/analyze"
        }:
            settings = get_settings()
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    oversized = int(content_length) > settings.max_request_body_bytes
                except ValueError:
                    oversized = True
                if oversized:
                    response = _error_response(request, 413, "request_too_large", "Request body is too large")
                    response.headers["X-Request-ID"] = request_id
                    return response
            if request.method == "POST" and request.url.path in {
                "/api/v1/chat", "/api/v1/chat/stream", "/api/v1/analyze"
            }:
                # Content-Length is optional for chunked uploads. Wrap ASGI
                # receive so the limit is enforced before Pydantic buffers the
                # complete request body.
                original_receive = request._receive
                received_bytes = 0

                async def limited_receive():
                    nonlocal received_bytes
                    message = await original_receive()
                    if message.get("type") == "http.request":
                        received_bytes += len(message.get("body", b""))
                        if received_bytes > settings.max_request_body_bytes:
                            raise _BodyLimitExceededError
                    return message

                request._receive = limited_receive
            identity = request.headers.get("authorization", "")
            if not identity:
                identity = request.client.host if request.client else "unknown"
            key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            if _rate_limiter is None:
                _rate_limiter = _build_rate_limiter(settings)
            if not await _rate_limiter.allow(key):
                response = _error_response(request, 429, "rate_limited", "Too many requests")
                response.headers["Retry-After"] = str(settings.rate_limit_window_seconds)
                response.headers["X-Request-ID"] = request_id
                return response
            if _cost_quota is None:
                _cost_quota = _build_cost_quota(settings)
            # Charge a conservative pre-provider estimate. Exact/policy routes
            # remain below the same ceiling, while a client cannot bypass the
            # daily budget by omitting Content-Length or opening many streams.
            body_units = max(256, min(4_096, (int(content_length) if content_length else 1_024) // 4))
            if not await _cost_quota.allow(key, body_units):
                response = _error_response(request, 429, "cost_quota_exceeded", "Usage quota exceeded")
                response.headers["Retry-After"] = str(settings.cost_quota_window_seconds)
                response.headers["X-Request-ID"] = request_id
                return response
        response = await call_next(request)
    except _BodyLimitExceededError:
        response = _error_response(request, 413, "request_too_large", "Request body is too large")
        response.headers["X-Request-ID"] = request_id
        return response
    except Exception as error:
        logger.exception("Unhandled middleware error", exc_info=error)
        response = _error_response(request, 500, "internal_error", "An unexpected error occurred")
    metrics.inc(
        "http_requests_total",
        method=request.method,
        path=request.url.path,
        status=str(response.status_code),
    )
    metrics.observe(
        "http_request_duration_seconds",
        time.perf_counter() - started,
        method=request.method,
        path=request.url.path,
    )
    response.headers["X-Request-ID"] = request_id
    return response


def _error_response(request: Request, status_code: int, code: str, message: str) -> JSONResponse:
    body = ErrorResponse(
        code=code,
        message=message,
        request_id=getattr(request.state, "request_id", "unknown"),
    )
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


def _build_rate_limiter(settings) -> RateLimiter:
    if settings.rate_limit_redis_url:
        return RedisRateLimiter(
            url=settings.rate_limit_redis_url,
            limit=settings.rate_limit_requests,
            window_seconds=settings.rate_limit_window_seconds,
        )
    return InMemoryRateLimiter(
        limit=settings.rate_limit_requests,
        window_seconds=settings.rate_limit_window_seconds,
    )


def _build_cost_quota(settings) -> CostQuota:
    if settings.rate_limit_redis_url:
        return RedisCostQuota(
            url=settings.rate_limit_redis_url,
            limit=settings.cost_quota_units,
            window_seconds=settings.cost_quota_window_seconds,
        )
    return InMemoryCostQuota(
        limit=settings.cost_quota_units,
        window_seconds=settings.cost_quota_window_seconds,
    )


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
app.include_router(auth_router, prefix="/api/v1")


@app.get(
    "/",
    tags=["System"],
    summary="API Root",
    description="Welcome endpoint with links to documentation and health status.",
)
async def root():
    return {
        "status": "ok",
        "message": "MediPay Agent API is online",
        "docs": "/docs",
        "health": "/health",
        "ready": "/ready",
    }


@app.get(
    "/health",
    tags=["System"],
    summary="Check API liveness",
    description="Return immediately when the API process is running.",
)
async def health():
    return {"status": "ok", "env": settings.app_env}


@app.get("/metrics", include_in_schema=False)
async def metrics_endpoint(request: Request) -> PlainTextResponse:
    """Expose bounded metrics; production requires a dedicated scrape token."""
    configured = settings.metrics_token.strip()
    if settings.app_env == "production" and not configured:
        raise HTTPException(status_code=404, detail="Not found")
    if configured:
        supplied = request.headers.get("authorization", "")
        if not hmac.compare_digest(supplied, f"Bearer {configured}"):
            raise HTTPException(status_code=401, detail="Metrics authorization required")
    return PlainTextResponse(metrics.render(), media_type="text/plain; version=0.0.4")


@app.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse}},
    tags=["System"],
    summary="Check GraphRAG readiness",
    description="Check OpenAI, embedding, Supabase, Qdrant, and Neo4j dependencies.",
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
