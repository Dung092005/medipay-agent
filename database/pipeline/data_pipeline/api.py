"""FastAPI application exposing the active dataset as a read-only API.

Run locally with::

    python -m uvicorn data_pipeline.api:app --host 0.0.0.0 --port 8000

Importing this module does not call OpenAI or connect to Neo4j. Embedding and
graph access are requested lazily by the relevant retrieval adapter.
"""

from __future__ import annotations

import logging
import os
import secrets
import uuid
from collections.abc import Sequence
from threading import Lock
from typing import Annotated, Any, Protocol

import psycopg
from fastapi import APIRouter, Depends, FastAPI, Header, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from data_pipeline.api_models import (
    DatasetInfo,
    DocumentResponse,
    ErrorResponse,
    LegalUnitResponse,
    TableResponse,
    HealthResponse,
    RelationshipDirection,
    RelationshipResponse,
    RetrieveHit,
    RetrieveRequest,
    RetrieveResponse,
    SearchRequest,
    SearchResponse,
    StatsResponse,
)
from data_pipeline.api_repository import PsycopgReadRepository, ReadRepository
from data_pipeline.embedding import embed_query
from data_pipeline.retrieval import EvidenceHit, RetrievalChannel, build_query_plan, reciprocal_rank_fusion


LOGGER = logging.getLogger("data_pipeline.api")


class EmbeddingProvider(Protocol):
    def embed_query(self, query: str) -> Sequence[float]: ...


class LazyGraphEmbeddingProvider:
    """Reuse the verified corpus preprocessor without loading it at API startup."""

    def __init__(self) -> None:
        self._encode_lock = Lock()

    def embed_query(self, query: str) -> Sequence[float]:
        # Embedding requests use the configured OpenAI embedding provider.
        with self._encode_lock:
            return embed_query(query)


class ApiProblem(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class ReadApiService:
    def __init__(self, repository: ReadRepository, embeddings: EmbeddingProvider) -> None:
        self.repository = repository
        self.embeddings = embeddings

    def dataset(self) -> DatasetInfo:
        dataset = self.repository.current_dataset()
        if dataset is None:
            raise ApiProblem(503, "dataset_not_ready", "No active dataset is available")
        return dataset.public_info()

    def search(self, request: SearchRequest) -> SearchResponse:
        exact_search = getattr(self.repository, "exact_search", None)
        if exact_search is not None:
            exact_page = exact_search(
                request.query,
                category=request.category,
                status=request.status,
                limit=request.limit,
            )
            if exact_page is not None and exact_page.hits:
                return SearchResponse(dataset_version=exact_page.dataset_version, hits=exact_page.hits)
        try:
            vector = self.embeddings.embed_query(request.query)
        except ValueError as error:
            raise ApiProblem(422, "invalid_query", str(error)) from error
        except (OSError, RuntimeError) as error:
            raise ApiProblem(503, "embedding_unavailable", "Embedding model is unavailable") from error
        page = self.repository.search(
            vector,
            category=request.category,
            status=request.status,
            limit=request.limit,
        )
        if page is None:
            raise ApiProblem(503, "dataset_not_ready", "No active dataset is available")
        return SearchResponse(dataset_version=page.dataset_version, hits=page.hits)

    def retrieve(self, request: RetrieveRequest) -> RetrieveResponse:
        plan = build_query_plan(
            request.query,
            category=request.category.value if request.category else None,
            reference_date=request.reference_date,
            jurisdiction=request.jurisdiction,
        )
        channels: dict[RetrievalChannel, list[EvidenceHit]] = {}
        warnings: list[str] = []
        exact_search = getattr(self.repository, "exact_search", None)
        exact_page = exact_search(request.query, category=request.category, status=request.status, limit=request.limit) if exact_search else None
        if exact_page and exact_page.hits:
            channels[RetrievalChannel.EXACT] = [
                EvidenceHit(
                    evidence_id=hit.chunk_id, document_id=hit.document_id,
                    passage_id=hit.chunk_id, unit_id=hit.unit_id, text=hit.text,
                    channel=RetrievalChannel.EXACT, score=hit.score, rank=index,
                    citation={"title": hit.title, **hit.citation},
                ) for index, hit in enumerate(exact_page.hits, start=1)
            ]
        lexical_search = getattr(self.repository, "lexical_search", None)
        lexical_page = lexical_search(request.query, category=request.category, status=request.status, limit=request.limit) if lexical_search else None
        if lexical_page and lexical_page.hits:
            channels[RetrievalChannel.LEXICAL] = [
                EvidenceHit(
                    evidence_id=hit.chunk_id, document_id=hit.document_id,
                    passage_id=hit.chunk_id, unit_id=hit.unit_id, text=hit.text,
                    channel=RetrievalChannel.LEXICAL, score=hit.score, rank=index,
                    citation={"title": hit.title, **hit.citation},
                ) for index, hit in enumerate(lexical_page.hits, start=1)
            ]
        else:
            warnings.append("lexical_channel_returned_no_hits")
        try:
            vector = self.embeddings.embed_query(request.query)
            semantic_page = self.repository.search(vector, category=request.category, status=request.status, limit=request.limit)
        except (ValueError, OSError, RuntimeError) as error:
            semantic_page = None
            warnings.append(f"semantic_channel_unavailable: {type(error).__name__}")
        if semantic_page and semantic_page.hits:
            channels[RetrievalChannel.SEMANTIC] = [
                EvidenceHit(
                    evidence_id=hit.chunk_id, document_id=hit.document_id,
                    passage_id=hit.chunk_id, unit_id=hit.unit_id, text=hit.text,
                    channel=RetrievalChannel.SEMANTIC, score=hit.score, rank=index,
                    citation={"title": hit.title, **hit.citation},
                ) for index, hit in enumerate(semantic_page.hits, start=1)
            ]
        seed_hits = [hit for page in (exact_page, lexical_page, semantic_page) if page for hit in page.hits]
        seed_ids = list(dict.fromkeys(hit.document_id for hit in seed_hits))
        graph_expand = getattr(self.repository, "graph_expand", None)
        if graph_expand and seed_ids:
            graph_page = graph_expand(
                seed_ids,
                limit=request.limit,
                reference_date=request.reference_date,
                jurisdiction=request.jurisdiction,
            )
            if graph_page and graph_page.hits:
                channels[RetrievalChannel.LEGAL_GRAPH] = [
                    EvidenceHit(
                        evidence_id=hit.chunk_id, document_id=hit.document_id, passage_id=hit.chunk_id,
                        unit_id=hit.unit_id, text=hit.text, channel=RetrievalChannel.LEGAL_GRAPH,
                        score=hit.score, rank=index, citation={"title": hit.title, **hit.citation},
                    ) for index, hit in enumerate(graph_page.hits, start=1)
                ]
            else:
                warnings.append("legal_graph_channel_returned_no_hits")
        else:
            warnings.append("legal_graph_channel_unavailable")
        fused = reciprocal_rank_fusion(channels)
        dataset = self.repository.current_dataset()
        if dataset is None:
            raise ApiProblem(503, "dataset_not_ready", "No active dataset is available")
        hits = [RetrieveHit(
            evidence_id=hit.evidence_id, document_id=hit.document_id,
            passage_id=hit.passage_id, unit_id=hit.unit_id, text=hit.text,
            score=hit.score, channel=hit.channel.value, citation=hit.citation,
        ) for hit in fused[: request.limit]]
        return RetrieveResponse(dataset_version=dataset.dataset_version, query_plan=plan.model_dump(mode="json"), hits=hits, warnings=warnings)

    def document(self, document_id: str, *, include_content: bool) -> DocumentResponse:
        document = self.repository.get_document(document_id, include_content=include_content)
        if document is None:
            raise ApiProblem(404, "document_not_found", "Document was not found")
        return document

    def document_html(self, document_id: str) -> Response:
        result = self.repository.get_document_html(document_id)
        if result is None:
            raise ApiProblem(404, "document_not_found", "Document was not found")
        dataset_version, raw_html, raw_html_sha256 = result
        return Response(
            content=raw_html,
            media_type="text/html",
            headers={
                "X-Dataset-Version": dataset_version,
                "X-Raw-HTML-SHA256": raw_html_sha256,
                "Content-Disposition": "inline",
            },
        )

    def legal_unit(self, unit_id: str) -> LegalUnitResponse:
        unit = self.repository.get_legal_unit(unit_id)
        if unit is None:
            raise ApiProblem(404, "legal_unit_not_found", "Legal unit was not found")
        return unit

    def table(self, table_id: str, *, cell_limit: int) -> TableResponse:
        table = self.repository.get_table(table_id, cell_limit=cell_limit)
        if table is None:
            raise ApiProblem(404, "table_not_found", "Table was not found")
        return table

    def relationships(
        self,
        document_id: str,
        *,
        direction: RelationshipDirection,
        limit: int,
    ) -> RelationshipResponse:
        page = self.repository.relationships(document_id, direction=direction, limit=limit)
        if page is None:
            raise ApiProblem(404, "document_not_found", "Document was not found")
        return RelationshipResponse(
            dataset_version=page.dataset_version,
            document_id=document_id,
            direction=direction,
            relationships=page.items,
        )

    def stats(self) -> StatsResponse:
        stats = self.repository.stats()
        if stats is None:
            raise ApiProblem(503, "dataset_not_ready", "No active dataset is available")
        return stats


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def _error_response(request: Request, status_code: int, code: str, message: str) -> JSONResponse:
    body = ErrorResponse(code=code, message=message, request_id=_request_id(request))
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


def create_app(
    *,
    repository: ReadRepository | None = None,
    embeddings: EmbeddingProvider | None = None,
    api_key: str | None = None,
) -> FastAPI:
    repository = repository or PsycopgReadRepository()
    embeddings = embeddings or LazyGraphEmbeddingProvider()
    configured_key = os.getenv("API_KEY") if api_key is None else api_key
    service = ReadApiService(repository, embeddings)

    application = FastAPI(
        title="BHYT / viện phí retrieval API",
        version="1.0.0",
        description="Read-only access to the currently active, versioned legal dataset.",
    )
    application.state.service = service

    cors_origins = [
        origin.strip()
        for origin in os.getenv("API_CORS_ORIGINS", "").split(",")
        if origin.strip()
    ]
    if cors_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", "X-API-Key", "X-Request-ID"],
        )

    @application.middleware("http")
    async def attach_request_id(request: Request, call_next: Any) -> Any:
        supplied = request.headers.get("X-Request-ID", "").strip()
        request.state.request_id = supplied[:128] if supplied else uuid.uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @application.exception_handler(ApiProblem)
    async def handle_api_problem(request: Request, error: ApiProblem) -> JSONResponse:
        return _error_response(request, error.status_code, error.code, error.message)

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        LOGGER.info("Invalid API request", extra={"request_id": _request_id(request), "errors": error.errors()})
        return _error_response(request, 422, "invalid_request", "Request validation failed")

    @application.exception_handler(psycopg.Error)
    async def handle_database_error(request: Request, error: psycopg.Error) -> JSONResponse:
        LOGGER.warning("Database request failed", exc_info=error, extra={"request_id": _request_id(request)})
        return _error_response(request, 503, "data_unavailable", "Dataset storage is unavailable")

    @application.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, error: Exception) -> JSONResponse:
        LOGGER.exception("Unhandled API error", exc_info=error, extra={"request_id": _request_id(request)})
        return _error_response(request, 500, "internal_error", "An unexpected error occurred")

    async def require_api_key(
        provided: Annotated[str | None, Header(alias="X-API-Key")] = None,
    ) -> None:
        if configured_key and (provided is None or not secrets.compare_digest(provided, configured_key)):
            raise ApiProblem(401, "unauthorized", "A valid API key is required")

    @application.get("/health/live", response_model=HealthResponse, tags=["health"])
    async def live() -> HealthResponse:
        return HealthResponse(status="ok")

    @application.get(
        "/health/ready",
        response_model=HealthResponse,
        responses={503: {"model": HealthResponse}},
        tags=["health"],
    )
    async def ready() -> HealthResponse | JSONResponse:
        try:
            dataset = repository.current_dataset()
            healthy = repository.ping()
        except (OSError, psycopg.Error):
            dataset = None
            healthy = False
        if not healthy or dataset is None:
            return JSONResponse(status_code=503, content={"status": "not_ready", "dataset_version": None})
        return HealthResponse(status="ok", dataset_version=dataset.dataset_version)

    router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)])

    @router.get(
        "/datasets/current",
        response_model=DatasetInfo,
        responses={401: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
        tags=["dataset"],
    )
    async def current_dataset() -> DatasetInfo:
        return service.dataset()

    @router.post(
        "/search",
        response_model=SearchResponse,
        responses={401: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
        tags=["retrieval"],
    )
    async def search(request: SearchRequest) -> SearchResponse:
        return service.search(request)

    @router.post(
        "/retrieve",
        response_model=RetrieveResponse,
        responses={401: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
        tags=["retrieval"],
    )
    async def retrieve(request: RetrieveRequest) -> RetrieveResponse:
        return service.retrieve(request)

    @router.get(
        "/documents/{document_id}",
        response_model=DocumentResponse,
        responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
        tags=["documents"],
    )
    async def document(
        document_id: Annotated[str, Path(min_length=1, max_length=120)],
        include_content: Annotated[bool, Query()] = False,
    ) -> DocumentResponse:
        return service.document(document_id, include_content=include_content)

    @router.get(
        "/documents/{document_id}/html",
        response_class=Response,
        responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
        tags=["documents"],
    )
    async def document_html(
        document_id: Annotated[str, Path(min_length=1, max_length=120)],
    ) -> Response:
        return service.document_html(document_id)

    @router.get(
        "/documents/{document_id}/relationships",
        response_model=RelationshipResponse,
        responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
        tags=["documents"],
    )
    async def relationships(
        document_id: Annotated[str, Path(min_length=1, max_length=120)],
        direction: Annotated[RelationshipDirection, Query()] = RelationshipDirection.BOTH,
        limit: Annotated[int, Query(ge=1, le=300)] = 100,
    ) -> RelationshipResponse:
        return service.relationships(document_id, direction=direction, limit=limit)

    @router.get(
        "/legal-units/{unit_id}",
        response_model=LegalUnitResponse,
        responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
        tags=["evidence"],
    )
    async def legal_unit(
        unit_id: Annotated[str, Path(min_length=1, max_length=120)],
    ) -> LegalUnitResponse:
        return service.legal_unit(unit_id)

    @router.get(
        "/tables/{table_id}",
        response_model=TableResponse,
        responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
        tags=["evidence"],
    )
    async def table(
        table_id: Annotated[str, Path(min_length=1, max_length=120)],
        cell_limit: Annotated[int, Query(ge=1, le=10_000)] = 2_000,
    ) -> TableResponse:
        return service.table(table_id, cell_limit=cell_limit)

    @router.get(
        "/stats",
        response_model=StatsResponse,
        responses={401: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
        tags=["dataset"],
    )
    async def stats() -> StatsResponse:
        return service.stats()

    application.include_router(router)
    return application


app = create_app()
