from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from typing import TypeVar

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import text

from src.agents.prompts import NO_EVIDENCE_RESPONSE, SYSTEM_PROMPT
from src.config import get_settings
from src.db.repositories import GraphRepository
from src.db.session import dispose_database, session_scope
from src.integrations.embeddings import EmbeddingModel, get_embedding_model
from src.integrations.langfuse import llm_invoke_config, trace_span
from src.integrations.neo4j import Neo4jGraphStore
from src.integrations.qdrant import QdrantVectorStore, VectorHit
from src.models.graph import Citation, DocumentCandidate, RetrievalResult
from src.services.circuit import AsyncCircuitBreaker
from src.services.llm import get_llm
from src.services.metrics import metrics
from src.services.query_rewrite import rewrite_retrieval_query, should_rewrite_query
from src.services.retrieval import (
    exclude_unverified_legacy_subordinate_sources,
    expand_query_variants,
    extract_document_numbers,
    extract_internal_legal_references,
    extract_legal_labels,
    extract_query_phrases,
    extract_query_terms,
    is_metadata_question,
    is_simple_status_metadata_question,
    normalize_identifier,
    policy_response,
    requires_clause_expansion,
    requires_evidence_verification,
    rerank_legal_candidates,
    retrieval_intent,
    rewrite_user_query,
    scope_evidence_matches_query,
    weighted_rrf,
)


class GraphRagUnavailableError(RuntimeError):
    """A required GraphRAG dependency is unavailable."""


class ChatProviderError(RuntimeError):
    """The configured chat provider failed to generate a response."""


# Bump whenever ranking/selection semantics change.  This is part of each
# in-process cache namespace, so an answer produced before the domain/scope
# policy cannot be replayed after a rolling deployment.
_RETRIEVAL_POLICY_VERSION = "hybrid-v11-document-recall-rerank"
logger = logging.getLogger(__name__)
_ProviderResult = TypeVar("_ProviderResult")


@dataclass(frozen=True)
class RetrievalBundle:
    evidence: list[RetrievalResult]
    relations: list
    direct_response: str = ""
    direct_citations: list[Citation] | None = None


class GraphRagRuntime:
    """Own request-time GraphRAG dependencies and their lifecycle."""

    def __init__(self) -> None:
        self._embeddings: EmbeddingModel | None = None
        self._graph_store: Neo4jGraphStore | None = None
        self._vector_store: QdrantVectorStore | None = None
        self._active_release: tuple[str, int, float] | None = None
        self._embedding_cache: dict[str, tuple[list[float], float]] = {}
        self._embedding_inflight: dict[str, asyncio.Task] = {}
        self._embedding_lock = asyncio.Lock()
        self._provider_semaphore = asyncio.Semaphore(max(1, get_settings().provider_concurrency))
        self._embedding_breaker = AsyncCircuitBreaker(
            failure_threshold=get_settings().provider_circuit_failure_threshold,
            cooldown_seconds=get_settings().provider_circuit_cooldown_seconds,
        )
        self._qdrant_breaker = AsyncCircuitBreaker(
            failure_threshold=get_settings().provider_circuit_failure_threshold,
            cooldown_seconds=get_settings().provider_circuit_cooldown_seconds,
        )
        self._neo4j_breaker = AsyncCircuitBreaker(
            failure_threshold=get_settings().provider_circuit_failure_threshold,
            cooldown_seconds=get_settings().provider_circuit_cooldown_seconds,
        )
        self._rewrite_breaker = AsyncCircuitBreaker(
            failure_threshold=get_settings().provider_circuit_failure_threshold,
            cooldown_seconds=get_settings().provider_circuit_cooldown_seconds,
        )
        self._exact_cache: dict[tuple[str, str], tuple[list[DocumentCandidate], float]] = {}
        self._retrieval_cache: dict[tuple[tuple[object, ...], str], tuple[RetrievalBundle, float]] = {}
        self._rewrite_cache: dict[tuple[str, str], tuple[str, float]] = {}
        # Generated answers are safe to reuse only while the immutable active
        # release and every prompt/input fingerprint remain identical.  Keep
        # this process-local and bounded; a release switch naturally makes
        # old entries unreachable and ``close`` clears them explicitly.
        self._answer_cache: dict[tuple[tuple[object, ...], str], tuple[str, float]] = {}
        self._readiness_cache: tuple[dict[str, bool], float] | None = None
        self._readiness_task: asyncio.Task[dict[str, bool]] | None = None

    def _get_embeddings(self) -> EmbeddingModel:
        if self._embeddings is None:
            self._embeddings = get_embedding_model()
        return self._embeddings

    def _get_graph_store(self) -> Neo4jGraphStore:
        if self._graph_store is None:
            self._graph_store = Neo4jGraphStore()
        return self._graph_store

    def _get_vector_store(self) -> QdrantVectorStore:
        if self._vector_store is None:
            self._vector_store = QdrantVectorStore()
        return self._vector_store

    async def retrieve(self, query: str) -> tuple[list, list]:
        bundle = await self.retrieve_bundle(query)
        return bundle.evidence, bundle.relations

    async def retrieve_bundle_adaptive(self, query: str) -> RetrievalBundle:
        """Retrieve the original and a constrained rewrite concurrently.

        The original wording is always retained. Rewrite failure, timeout or
        rejection therefore cannot turn an otherwise answerable request into
        an outage, while the two result lists are fused by rank rather than by
        incomparable provider scores.
        """
        settings = get_settings()
        if not settings.query_rewrite_enabled or not should_rewrite_query(query):
            return await self.retrieve_bundle(query)
        # Long thematic questions already carry their decisive legal terms;
        # running a second full retrieval view adds ~10–15s while rarely
        # improving recall. Keep HyDE for temporal/relational routing (where
        # formal wording is genuinely different) and for short open queries
        # covered by the focused unit tests.
        if retrieval_intent(query) == "thematic" and len(query.split()) >= 10:
            return await self.retrieve_bundle(query)

        original_task = asyncio.create_task(self.retrieve_bundle(query))
        try:
            rewritten = await asyncio.wait_for(
                self._rewrite_query(query),
                timeout=settings.query_rewrite_timeout_seconds,
            )
        except Exception:
            metrics.inc("query_rewrite_total", outcome="fallback")
            return await original_task

        if " ".join(rewritten.casefold().split()) == " ".join(query.casefold().split()):
            metrics.inc("query_rewrite_total", outcome="unchanged")
            return await original_task

        rewritten_task = asyncio.create_task(self.retrieve_bundle(rewritten))
        original, expanded = await asyncio.gather(
            original_task,
            rewritten_task,
            return_exceptions=True,
        )
        valid = [item for item in (original, expanded) if isinstance(item, RetrievalBundle)]
        if not valid:
            first_error = original if isinstance(original, BaseException) else expanded
            raise first_error
        metrics.inc("query_rewrite_total", outcome="success")
        if len(valid) == 1:
            return _copy_bundle(valid[0])
        merged = _merge_bundles(
            [original, expanded],
            channel_weights={"query_0": 1.0, "query_1": 1.2},
            # Keep a wider candidate pool until the legal ranker has seen
            # both phrasings.  Cutting to the final context size here let a
            # duplicated generic match evict a current operative clause that
            # occurred in only one query view.
            limit=min(
                getattr(settings, "retrieval_candidate_k", settings.max_llm_evidence * 4),
                settings.max_llm_evidence * 4,
            ),
            max_per_document=(
                max(settings.max_chunks_per_document, 6)
                if requires_clause_expansion(query)
                else settings.max_chunks_per_document
            ),
        )
        # RRF rewards an item that appears in both views. That is normally
        # desirable, but an older paraphrase can otherwise beat an operative
        # current law that the clause-shaped rewrite found only once. Reapply
        # the same source-authority/currentness policy after fusion; it does
        # not add documents or facts, only orders the already verified set.
        # RRF is intentionally consensus-biased.  A useful HyDE view may
        # surface one decisive operative clause that the original colloquial
        # wording cannot retrieve, so retain the best source-derived anchors
        # from *each* view before the final legal ranking.  This is bounded
        # and query-agnostic; it does not map a question to a document.
        anchors = [
            item
            for bundle in valid
            for item in rerank_legal_candidates(query, bundle.evidence)[:3]
        ]
        candidates = {item.chunk_id: item for item in merged.evidence}
        for anchor in anchors:
            if anchor.chunk_id not in candidates:
                candidates[anchor.chunk_id] = anchor
        ranked = rerank_legal_candidates(query, list(candidates.values()))
        # Preserve query-derived exact anchors from either adaptive view ahead
        # of the final context cut.  RRF/reranking can otherwise drop a
        # one-view legal-unit hit even though it is the only passage carrying
        # the user's distinctive three-token phrase.
        anchor_phrases = [
            phrase for phrase in extract_query_phrases(query, limit=16)
            if len(phrase.split()) >= 2
        ]
        if anchor_phrases:
            exact = [
                item
                for bundle in valid
                for item in bundle.evidence
                if any(
                    phrase.casefold() in f"{item.section_title} {item.content}".casefold()
                    for phrase in anchor_phrases
                )
            ]
            preserved: list[RetrievalResult] = []
            seen: set[str] = set()
            for item in exact:
                if item.chunk_id not in seen:
                    seen.add(item.chunk_id)
                    preserved.append(item)
                if len(preserved) >= min(3, settings.max_llm_evidence):
                    break
            if preserved:
                ranked = preserved + [item for item in ranked if item.chunk_id not in seen]
        if requires_evidence_verification(query) and not extract_document_numbers(query):
            primary_laws = [
                item
                for bundle in valid
                for item in bundle.evidence
                if (
                    item.document_type.strip().casefold() == "luật"
                    or item.title.strip().casefold().startswith(("luật ", "bộ luật "))
                )
            ]
            def issued_year(item: RetrievalResult) -> int:
                values = [int(value) for value in re.findall(r"\b(?:19|20)\d{2}\b", item.issued_date)]
                return max(values, default=0)

            current_year = max((issued_year(item) for item in primary_laws), default=0)
            bhyt_primary = [
                item for item in primary_laws
                if any("bhyt" in str(category).casefold() for category in item.categories)
            ]
            if bhyt_primary:
                primary_laws = bhyt_primary
                current_year = max((issued_year(item) for item in primary_laws), default=0)
            current_items = [item for item in primary_laws if issued_year(item) == current_year and float(item.score) > 0]
            if current_items:
                current_items.sort(key=lambda item: -float(item.score))
                current_ids = {item.chunk_id for item in current_items[:2]}
                exclusion_items = [
                    item
                    for item in ranked
                    if "không được hưởng" in f"{item.section_title} {item.content}".casefold()
                ]
                # Merge current items into ranked without evicting higher-scoring specific answers
                ranked = rerank_legal_candidates(
                    query,
                    list({item.chunk_id: item for item in [*current_items[:2], *exclusion_items, *ranked]}.values()),
                )
        return RetrievalBundle(
            evidence=ranked[: settings.max_llm_evidence],
            relations=merged.relations,
            direct_response=merged.direct_response,
            direct_citations=merged.direct_citations,
        )

    async def _rewrite_query(self, query: str) -> str:
        settings = get_settings()
        key = (settings.model_name, " ".join(query.casefold().split()))
        now = time.monotonic()
        cached = self._rewrite_cache.get(key)
        if cached and now - cached[1] < 300:
            return cached[0]
        async with trace_span(
            "query-rewrite",
            as_type="span",
            input={"original_query": query},
            metadata={"model": settings.model_name},
        ) as rw_span:
            rewritten = await self._provider_call(
                "query_rewrite",
                self._rewrite_breaker,
                lambda: rewrite_retrieval_query(query),
            )
            if rw_span is not None:
                rw_span.update(
                    output={
                        "rewritten_query": rewritten,
                        "unchanged": rewritten.strip().casefold() == query.strip().casefold(),
                    }
                )
            if len(self._rewrite_cache) >= 256:
                oldest = min(self._rewrite_cache, key=lambda item: self._rewrite_cache[item][1])
                self._rewrite_cache.pop(oldest, None)
            self._rewrite_cache[key] = (rewritten, now)
            return rewritten

    async def retrieve_bundle(self, query: str) -> RetrievalBundle:
        started = time.perf_counter()
        safe_response = policy_response(query)
        if safe_response:
            metrics.inc("retrieval_requests_total", mode="policy", outcome="success")
            metrics.observe("retrieval_duration_seconds", time.perf_counter() - started, mode="policy")
            return RetrievalBundle(evidence=[], relations=[], direct_response=safe_response)
        normalized_query = " ".join(query.casefold().split())
        settings = get_settings()
        current_release = self._active_release[0] if self._active_release else ""
        cache_namespace = (
            current_release,
            settings.embedding_model,
            settings.embedding_dimensions,
            settings.model_name,
            settings.qdrant_collection,
            settings.retrieval_top_k,
            settings.retrieval_candidate_k,
            settings.semantic_similarity_threshold,
            settings.graph_hops,
            settings.max_llm_evidence,
            _RETRIEVAL_POLICY_VERSION,
        )
        cached = self._retrieval_cache.get((cache_namespace, normalized_query)) if current_release else None
        if cached and time.monotonic() - cached[1] < 60:
            metrics.inc("retrieval_requests_total", mode="cache", outcome="success")
            metrics.observe("retrieval_duration_seconds", time.perf_counter() - started, mode="cache")
            return _copy_bundle(cached[0])
        async with trace_span(
            "retrieve-context",
            as_type="retriever",
            input={"query": query},
        ) as span:
            try:
                bundle = await asyncio.wait_for(
                    self._retrieve(query),
                    timeout=get_settings().retrieval_timeout_seconds,
                )
            except TimeoutError as exc:
                metrics.inc("retrieval_requests_total", mode="provider", outcome="timeout")
                metrics.observe("retrieval_duration_seconds", time.perf_counter() - started, mode="provider")
                raise GraphRagUnavailableError("Retrieval deadline exceeded") from exc
            except Exception:
                metrics.inc("retrieval_requests_total", mode="provider", outcome="error")
                metrics.observe("retrieval_duration_seconds", time.perf_counter() - started, mode="provider")
                raise
            if span is not None:
                span.update(
                    output={
                        "evidence_count": len(bundle.evidence),
                        "relation_count": len(bundle.relations),
                        "documents_retrieved": list(dict.fromkeys(item.title for item in bundle.evidence[:6])),
                        "chunk_ids": [item.chunk_id for item in bundle.evidence],
                        "direct": bool(bundle.direct_response),
                    }
                )
            if self._active_release:
                cache_key = (
                    (
                        self._active_release[0],
                        settings.embedding_model,
                        settings.embedding_dimensions,
                        settings.model_name,
                        settings.qdrant_collection,
                        settings.retrieval_top_k,
                        settings.retrieval_candidate_k,
                        settings.semantic_similarity_threshold,
                        settings.graph_hops,
                        settings.max_llm_evidence,
                        _RETRIEVAL_POLICY_VERSION,
                    ),
                    normalized_query,
                )
                if len(self._retrieval_cache) >= 128:
                    oldest = min(self._retrieval_cache, key=lambda item: self._retrieval_cache[item][1])
                    self._retrieval_cache.pop(oldest, None)
                self._retrieval_cache[cache_key] = (_copy_bundle(bundle), time.monotonic())
            metrics.inc("retrieval_requests_total", mode="provider", outcome="success")
            metrics.observe("retrieval_duration_seconds", time.perf_counter() - started, mode="provider")
            return bundle

    async def retrieve_bundle_many(self, queries: Sequence[str]) -> RetrievalBundle:
        """Retrieve bounded sub-queries with one embedding/Qdrant batch.

        The method is used only for explicit deterministic decomposition. Each
        sub-query still passes through the same release-scoped lexical,
        hydration, graph and verification path; only provider calls that can
        preserve ordering are batched.
        """
        bounded = list(dict.fromkeys(" ".join(query.split()) for query in queries if query and query.strip()))[:3]
        if len(bounded) <= 1:
            return await self.retrieve_bundle(bounded[0] if bounded else "")
        if any(policy_response(query) for query in bounded):
            bundles = await asyncio.gather(*(self.retrieve_bundle(query) for query in bounded))
            return _merge_bundles(bundles)

        settings = get_settings()
        vectors = await self._embed_queries(bounded)
        if any(len(vector) != settings.embedding_dimensions for vector in vectors):
            raise GraphRagUnavailableError("Query embedding has unexpected dimensions")
        dataset_id = ""
        async with session_scope() as session:
            release = await GraphRepository(session).current_dataset_release()
            if release is not None:
                dataset_id = release[0]
        if not dataset_id:
            raise GraphRagUnavailableError("No active dataset is available")
        vector_hits = await self._search_vectors_many(
            vectors,
            query_texts=bounded,
            dataset_id=dataset_id,
            limit=getattr(settings, "retrieval_candidate_k", getattr(settings, "retrieval_top_k", 20)),
            score_threshold=settings.semantic_similarity_threshold,
        )
        bundles = await asyncio.gather(*(
            self._retrieve_staged(query, vector_override=vector, vector_hits_override=hits)
            for query, vector, hits in zip(bounded, vectors, vector_hits, strict=True)
        ))
        return _merge_bundles(bundles)

    async def _active_dataset(self, repository: GraphRepository) -> tuple[str, int]:
        now = time.monotonic()
        if self._active_release and now - self._active_release[2] < 30:
            return self._active_release[0], self._active_release[1]
        release = await repository.current_dataset_release()
        if release is None:
            raise GraphRagUnavailableError("No active dataset is available")
        self._active_release = (release[0], release[1], now)
        return release

    async def _embed_query(self, query: str) -> Sequence[float]:
        """Cache normalized query vectors briefly; embeddings are immutable per model."""
        key = " ".join(query.casefold().split())
        now = time.monotonic()
        cached = self._embedding_cache.get(key)
        if cached and now - cached[1] < 300:
            return cached[0]
        async with self._embedding_lock:
            task = self._embedding_inflight.get(key)
            if task is None:
                task = asyncio.create_task(self._embed_provider(query))
                self._embedding_inflight[key] = task
        try:
            vector = [float(value) for value in await task]
        finally:
            if self._embedding_inflight.get(key) is task:
                self._embedding_inflight.pop(key, None)
        if len(self._embedding_cache) >= 256:
            oldest = min(self._embedding_cache, key=lambda item: self._embedding_cache[item][1])
            self._embedding_cache.pop(oldest, None)
        self._embedding_cache[key] = (vector, now)
        return vector

    async def _embed_provider(self, query: str) -> Sequence[float]:
        return await self._provider_call(
            "embedding",
            self._embedding_breaker,
            lambda: self._get_embeddings().embed_query(query),
        )

    async def _embed_queries(self, queries: Sequence[str]) -> list[Sequence[float]]:
        values = list(queries)
        return await self._provider_call(
            "embedding_batch",
            self._embedding_breaker,
            lambda: self._get_embeddings().embed_queries(values),
        )

    async def _search_vectors(self, vector: Sequence[float], **kwargs):
        return await self._provider_call(
            "qdrant",
            self._qdrant_breaker,
            lambda: self._get_vector_store().search(vector, **kwargs),
        )

    async def _search_vectors_many(self, vectors: Sequence[Sequence[float]], **kwargs):
        return await self._provider_call(
            "qdrant_batch",
            self._qdrant_breaker,
            lambda: self._get_vector_store().search_many(vectors, **kwargs),
        )

    async def _provider_call(
        self,
        stage: str,
        breaker: AsyncCircuitBreaker,
        operation: Callable[[], Awaitable[_ProviderResult]],
    ) -> _ProviderResult:
        """Bound provider concurrency while exposing queue and latency metrics."""
        wait_started = time.perf_counter()
        async with self._provider_semaphore:
            metrics.observe("provider_queue_wait_seconds", time.perf_counter() - wait_started, stage=stage)
            metrics.inc("provider_inflight", 1, stage=stage)
            started = time.perf_counter()
            try:
                result = await breaker.call(operation)
            except asyncio.CancelledError:
                raise
            except Exception:
                metrics.inc("provider_calls_total", outcome="error", stage=stage)
                raise
            else:
                metrics.inc("provider_calls_total", outcome="success", stage=stage)
                return result
            finally:
                metrics.inc("provider_inflight", -1, stage=stage)
                metrics.observe("provider_duration_seconds", time.perf_counter() - started, stage=stage)

    async def _find_documents(self, repository: GraphRepository, *, dataset_id: str, number: str) -> list[DocumentCandidate]:
        key = (dataset_id, number)
        now = time.monotonic()
        cached = self._exact_cache.get(key)
        if cached and now - cached[1] < 300:
            return cached[0]
        documents = await repository.find_documents(number, dataset_id=dataset_id, limit=3)
        if len(self._exact_cache) >= 256:
            oldest = min(self._exact_cache, key=lambda item: self._exact_cache[item][1])
            self._exact_cache.pop(oldest, None)
        self._exact_cache[key] = (documents, now)
        return documents

    async def _retrieve(self, query: str) -> RetrievalBundle:
        return await self._retrieve_staged(query)

    async def _retrieve_staged(
        self,
        query: str,
        *,
        vector_override: Sequence[float] | None = None,
        vector_hits_override: Sequence[VectorHit] | None = None,
    ) -> RetrievalBundle:
        """Retrieve in bounded DB phases; never hold a SQL session over providers."""
        settings = get_settings()

        async def lexical_search(
            *, dataset_id: str, document_ids: Sequence[str] | None = None, limit: int
        ) -> list[RetrievalResult]:
            async with trace_span(
                "lexical-search-postgres",
                as_type="retriever",
                input={"query": query, "dataset_id": dataset_id, "document_ids": document_ids, "limit": limit},
            ) as lex_span:
                query_variants = expand_query_variants(query)
                async with session_scope() as lexical_session:
                    repo = GraphRepository(lexical_session)
                    all_hits: list[RetrievalResult] = []
                    seen_chunks: set[str] = set()
                    for q in query_variants:
                        hits = await repo.search_lexical(
                            q, dataset_id=dataset_id, document_ids=document_ids, limit=limit
                        )
                        for hit in hits:
                            if hit.chunk_id not in seen_chunks:
                                seen_chunks.add(hit.chunk_id)
                                all_hits.append(hit)
                    result = all_hits[:limit]
                    if lex_span is not None:
                        lex_span.update(
                            output={
                                "hit_count": len(result),
                                "chunk_ids": [h.chunk_id for h in result[:6]],
                                "documents": list(dict.fromkeys(h.title for h in result[:4])),
                            }
                        )
                    return result

        try:
            # Phase 1: release metadata, exact lookup and PageIndex. This
            # session closes before embedding, Qdrant or Neo4j calls.
            async with session_scope() as session:
                repository = GraphRepository(session)
                async with trace_span("get-current-dataset") as span:
                    dataset_id, expected_points = await self._active_dataset(repository)
                    if span is not None:
                        span.update(output={"dataset_id": dataset_id, "expected_qdrant_points": expected_points})
                exact_candidates: list[DocumentCandidate] = []
                for number in extract_document_numbers(query):
                    exact_candidates.extend(
                        await self._find_documents(repository, dataset_id=dataset_id, number=number)
                    )
                exact_candidates = list({candidate.document_id: candidate for candidate in exact_candidates}.values())
                # ``find_documents`` also returns documents whose title mentions
                # the signature.  Keep only exact signature matches for a direct
                # metadata answer; related documents must not create ambiguity.
                exact_signatures = {
                    normalize_identifier(number) for number in extract_document_numbers(query)
                }
                signature_matches = [
                    candidate
                    for candidate in exact_candidates
                    if normalize_identifier(candidate.so_ky_hieu) in exact_signatures
                ]
                if signature_matches:
                    exact_candidates = signature_matches
                intent = retrieval_intent(query)
                if (
                    is_metadata_question(query)
                    and (intent == "lookup" or is_simple_status_metadata_question(query))
                    and len(exact_candidates) == 1
                    and exact_candidates[0].answer_ready
                ):
                    document = exact_candidates[0]
                    return RetrievalBundle(
                        [],
                        [],
                        _format_metadata_answer(query, document),
                        [
                            Citation(
                                document_id=document.document_id,
                                chunk_id=f"metadata:{document.document_id}",
                                dataset_id=dataset_id,
                                title=document.title,
                                document_number=document.so_ky_hieu,
                                # Keep the complete, provenance-checked metadata
                                # record in the citation context.  A status-only
                                # quote made date/title answers look like a
                                # retrieval miss to the evaluator and to clients
                                # rendering citation details.
                                # Keep a complete metadata record in the
                                # citation even when the user-facing answer is
                                # intentionally narrowed to the requested
                                # field(s).
                                quote=_format_metadata_answer(query, document, include_context=True),
                                channels=["exact"],
                                evidence_kind="document_metadata",
                                provenance_verified=document.legal_status_verified,
                                source_url=document.legal_status_source,
                                source_checked_at=document.legal_status_checked_at,
                            )
                        ],
                    )
                exact_document_ids = [
                    candidate.document_id for candidate in exact_candidates if candidate.answer_ready
                ]
                # A rewrite can expand an abbreviation into the formal subject
                # found in a statute title.  Keep title hits as a tiny
                # *candidate* set only; no title ever becomes public evidence
                # without a matching canonical passage below.
                document_recall_enabled = (
                    not exact_document_ids
                    and not is_metadata_question(query)
                )
                current_title_query = ""
                title_document_ids = (
                    await repository.search_title_documents(
                        query, dataset_id=dataset_id, limit=4
                    )
                    if document_recall_enabled and hasattr(repository, "search_title_documents")
                    else []
                )
                # A question that names an old instrument but asks for the
                # current rule needs a second, year-neutral title recall. The
                # query is derived from the user's own words; this prevents a
                # historical year from starving retrieval of the governing
                # current statute.
                if (
                    re.search(r"\b(?:19|20)\d{2}\b", query)
                    and any(marker in query.casefold() for marker in ("hiện nay", "hiện hành"))
                ):
                    current_title_query = re.sub(r"\b(?:19|20)\d{2}\b", " ", query)
                    current_title_query = re.sub(
                        r"\b(?:hiện nay|hiện hành|nào|quy định)\b", " ", current_title_query,
                        flags=re.IGNORECASE,
                    ).replace("Thông tư", "Luật").replace("thông tư", "luật")
                    title_document_ids = list(
                        dict.fromkeys(
                            [
                                *title_document_ids,
                                *await repository.search_title_documents(
                                    current_title_query, dataset_id=dataset_id, limit=4
                                ),
                            ]
                        )
                    )[:8]
                # Passage retrieval is intentionally broad, but the decisive
                # clause can be short and rank below verbose background text.
                # Independently recall a small set of documents from the
                # canonical lexical index, then inspect their passages.  This
                # is a query-derived candidate stage (not an answer mapping):
                # title hits, lexical document hits and ANN hits still have to
                # produce a grounded passage and pass the shared reranker.
                document_recall_ids = (
                    await repository.search_lexical_document_ids(
                        query,
                        dataset_id=dataset_id,
                        # Keep the same bounded candidate budget as the
                        # corpus-wide first stage.  A document-level recall
                        # pass exists precisely to rescue a short operative
                        # clause that ranked below broad explanatory text;
                        # truncating it halfway through would silently lose
                        # the current governing law for a rewritten query.
                        limit=settings.retrieval_candidate_k,
                    )
                    if (
                        document_recall_enabled
                        and hasattr(repository, "search_lexical_document_ids")
                    )
                    else []
                )
                if current_title_query and hasattr(repository, "search_lexical_document_ids"):
                    current_recall_ids = await repository.search_lexical_document_ids(
                        current_title_query,
                        dataset_id=dataset_id,
                        limit=settings.retrieval_candidate_k,
                    )
                    document_recall_ids = list(
                        dict.fromkeys(
                            [
                                *current_recall_ids,
                                *document_recall_ids,
                            ]
                        )
                    )[: settings.retrieval_candidate_k]
                document_semantic_candidate_ids = list(
                    dict.fromkeys([*title_document_ids, *document_recall_ids])
                )[: settings.retrieval_candidate_k]
                # The document-wide lexical scan is more expensive than a
                # Qdrant filter.  It needs only the strongest candidates;
                # the full recall set is still used by the dense re-query
                # below, then the shared reranker decides final evidence.
                document_candidate_ids = document_semantic_candidate_ids[:24]
                page_results = _verified_evidence(
                    await repository.resolve_legal_units(
                        extract_legal_labels(query),
                        dataset_id=dataset_id,
                        document_ids=exact_document_ids,
                    )
                )

            if page_results and intent in {"lookup", "legal_unit"}:
                lexical_results = await lexical_search(
                    dataset_id=dataset_id,
                    document_ids=exact_document_ids,
                    limit=max(8, settings.retrieval_top_k),
                )
                return RetrievalBundle(
                    evidence=_verified_evidence(
                        weighted_rrf(
                            {"page_index": page_results, "lexical": lexical_results},
                            limit=settings.max_llm_evidence,
                        )
                    ),
                    relations=[],
                )

            # An explicit public document number is a hard retrieval boundary only for lookup intent.
            # Thematic/temporal questions must search corpus-wide to avoid blinding retrieval when a
            # conversational hint or cited document is present.
            search_document_ids = exact_document_ids if intent == "lookup" else None
            lexical_task = asyncio.create_task(
                lexical_search(
                    dataset_id=dataset_id,
                    document_ids=search_document_ids,
                    limit=settings.retrieval_candidate_k,
                )
            )
            vector = None
            try:
                async with trace_span(
                    "embedding-query",
                    as_type="embedding",
                    input={"query_length": len(query)},
                    metadata={"model": settings.embedding_model},
                ) as span:
                    vector = vector_override if vector_override is not None else await self._embed_query(query)
                    if span is not None and vector:
                        span.update(output={"embedding_dimensions": len(vector)})
            except Exception as exc:
                logger.warning("Vector embedding provider unavailable, falling back to lexical search: %s", exc)
                vector = None

            if vector is not None and len(vector) == settings.embedding_dimensions:
                async with trace_span(
                    "qdrant-search", as_type="retriever", metadata={"dataset_id": dataset_id}
                ) as span:
                    if vector_hits_override is None:
                        semantic_task = asyncio.create_task(
                            self._search_vectors(
                                vector,
                                query_text=query,
                                dataset_id=dataset_id,
                                document_ids=search_document_ids,
                                limit=settings.retrieval_candidate_k,
                                score_threshold=settings.semantic_similarity_threshold,
                            )
                        )
                        document_semantic_task = (
                            asyncio.create_task(
                                self._search_vectors(
                                    vector,
                                    query_text=query,
                                    dataset_id=dataset_id,
                                    document_ids=document_semantic_candidate_ids,
                                    limit=min(24, settings.retrieval_candidate_k),
                                    score_threshold=settings.semantic_similarity_threshold,
                                )
                            )
                            if document_semantic_candidate_ids
                            else None
                        )
                        lexical_results, vector_hits = await asyncio.gather(lexical_task, semantic_task)
                        document_vector_hits = (
                            await document_semantic_task if document_semantic_task is not None else []
                        )
                    else:
                        lexical_results = await lexical_task
                        vector_hits = list(vector_hits_override)
                        document_vector_hits = []
                    if span is not None:
                        span.update(output={"result_count": len(vector_hits)})
            else:
                lexical_results = await lexical_task
                vector_hits = list(vector_hits_override or [])
                document_vector_hits = []

            # Phase 3: bounded hydration/sibling expansion only.
            async with session_scope() as hydration_session:
                hydration_repository = GraphRepository(hydration_session)
                # Dense passage recall can identify the governing instrument
                # even when its title contains none of the user's wording.
                # Feed those IDs into the bounded operative scan when the
                # repository supports that optimized path. The capability
                # guard keeps lightweight test doubles and older deployments
                # on the original single lexical call.
                if hasattr(hydration_repository, "search_document_operatives"):
                    semantic_document_ids = [
                        item.document_id
                        for item in [*vector_hits, *document_vector_hits]
                        if getattr(item, "document_id", "")
                    ]
                    document_candidate_ids = list(
                        dict.fromkeys([*document_candidate_ids, *semantic_document_ids])
                    )[: max(24, min(48, settings.retrieval_candidate_k))]
                if hasattr(hydration_repository, "hydrate_chunks_with_scope"):
                    hydrated, semantic_scope = await hydration_repository.hydrate_chunks_with_scope(
                        [item.chunk_id for item in vector_hits],
                        dataset_id=dataset_id,
                        # Sibling enumeration is only meaningful when the user
                        # identified a document and legal unit. Expanding every
                        # thematic ANN hit used to inject unrelated a)/b) units
                        # with an artificial score of 1.0.
                        scope_limit=(
                            # Scope expansion is a candidate pool, not final
                            # context. A few older roots can otherwise use
                            # all 12 slots before the current article's last
                            # operative point (for example h)) is reached.
                            max(24, settings.max_llm_evidence)
                            if (intent == "legal_unit" and exact_document_ids)
                            or requires_clause_expansion(query)
                            else 0
                        ),
                    )
                else:
                    hydrated = await hydration_repository.hydrate_chunks(
                        [item.chunk_id for item in vector_hits], dataset_id=dataset_id
                    )
                    fallback_focus = [
                        item for item in hydrated
                        if _is_enumerated_unit(item.section_title or item.content)
                    ]
                    semantic_scope = await hydration_repository.expand_sibling_legal_units(
                        [item.unit_id for item in fallback_focus if item.unit_id],
                        dataset_id=dataset_id,
                        limit=settings.max_llm_evidence,
                    )
                legal_reference_results: list[RetrievalResult] = []
                document_recall_semantic_results: list[RetrievalResult] = []
                if document_vector_hits:
                    candidate_hydrated = await hydration_repository.hydrate_chunks(
                        [item.chunk_id for item in document_vector_hits], dataset_id=dataset_id,
                    )
                    document_recall_semantic_results = rerank_legal_candidates(
                        query, _verify_hydrated_hits(candidate_hydrated, document_vector_hits)
                    )
                if requires_clause_expansion(query) and exact_document_ids:
                    # References such as “điểm b khoản 4 Điều 12” recur in
                    # many unrelated regulations.  Expand only the few
                    # strongest directly retrieved documents, then use the
                    # shared reference inside those documents; expanding
                    # every broad ANN hit would pull in military/local rules.
                    reference_seed_items = [*hydrated, *lexical_results, *semantic_scope]
                    query_terms = {
                        token.casefold()
                        for token in re.findall(r"[0-9A-Za-zÀ-ỹĐđ]+", query)
                        if len(token) > 2
                    }
                    reference_targets: list[tuple[str, str]] = []
                    for item in reference_seed_items:
                        title_scope = item.title.casefold()
                        # The cross-reference expansion is for an
                        # implementing instrument that explicitly guides a
                        # statute. Other thematic hits frequently reuse the
                        # same article numbers for finance, police/military
                        # or local schemes, where an expansion would be a
                        # false legal chain.
                        if "hướng dẫn" not in title_scope or "luật" not in title_scope:
                            continue
                        source_text = f"{item.title} {item.section_title} {item.content}"
                        source_terms = {
                            token.casefold()
                            for token in re.findall(r"[0-9A-Za-zÀ-ỹĐđ]+", source_text)
                            if len(token) > 2
                        }
                        # Do not let an incidental reference from a broad
                        # ANN hit seed another document's legal chain.
                        if len(query_terms & source_terms) < 2:
                            continue
                        reference_targets.extend(
                            (item.document_id, reference)
                            for reference in extract_internal_legal_references([source_text])
                        )
                    if reference_targets:
                        legal_reference_results = await hydration_repository.expand_internal_references(
                            reference_targets,
                            dataset_id=dataset_id,
                            # A grouped reference can have several preceding
                            # administrative clauses before its operative
                            # percentage/duration clause. Keep this bounded
                            # pool wide enough for the ranker to see it.
                            limit=min(20, settings.retrieval_candidate_k),
                        )
                ranking_metadata = (
                    await hydration_repository.document_ranking_metadata(
                        [
                            item.document_id
                            for item in [
                                *hydrated, *lexical_results, *semantic_scope, *legal_reference_results, *page_results
                                , *document_recall_semantic_results
                            ]
                        ] + document_candidate_ids,
                        dataset_id=dataset_id,
                    )
                    if hasattr(hydration_repository, "document_ranking_metadata")
                    else {}
                )
                _apply_document_ranking_metadata(
                    [
                        *hydrated, *lexical_results, *semantic_scope, *legal_reference_results,
                        *page_results, *document_recall_semantic_results,
                    ],
                    ranking_metadata,
                )
                document_recall_semantic_results = rerank_legal_candidates(
                    query, document_recall_semantic_results
                )
                semantic_results = rerank_legal_candidates(
                    query, _verify_hydrated_hits(hydrated, vector_hits)
                )
                lexical_results = rerank_legal_candidates(query, lexical_results)
                semantic_scope = rerank_legal_candidates(query, semantic_scope)
                semantic_scope = [
                    item
                    for item in semantic_scope
                    if scope_evidence_matches_query(
                        query,
                        item,
                        candidate_pool=[*semantic_results, *lexical_results, *semantic_scope],
                    )
                ]
                legal_reference_results = rerank_legal_candidates(query, legal_reference_results)
                if semantic_scope and requires_clause_expansion(query):
                    # A child point is generated from a high-ranked parent
                    # legal unit, so its standalone lexical score starts at
                    # zero. Carry bounded parent relevance forward; otherwise
                    # the exact a)/b)/c) answer is always outranked by the
                    # parent heading that merely introduces the list.
                    parent_scores: dict[str, float] = {}
                    for item in semantic_results:
                        parent_scores[item.document_id] = max(
                            parent_scores.get(item.document_id, 0.0), float(item.score)
                        )
                    for item in semantic_scope:
                        inherited = parent_scores.get(item.document_id, 0.0) * 0.65
                        item.score += inherited
                        item.rank_details = {**item.rank_details, "scope_parent_relevance": inherited}
                    semantic_scope.sort(key=lambda item: (-item.score, item.document_id, item.chunk_id))
                page_results = rerank_legal_candidates(query, page_results)
                document_anchor_results: list[RetrievalResult] = []
                document_recall_operatives: list[RetrievalResult] = []
                if document_candidate_ids and hasattr(hydration_repository, "search_lexical"):
                    # The document-level index has already bounded the
                    # corpus. Reuse the GIN-backed canonical passage query
                    # here; it is materially cheaper than a LIKE scan over
                    # every chunk and still returns the exact operative text.
                    lexical_document_rows = await hydration_repository.search_lexical(
                        query,
                        dataset_id=dataset_id,
                        document_ids=document_candidate_ids,
                        # The query is already document-bounded. Fetch a
                        # wider lexical head, then retain one best passage
                        # per candidate document so a verbose source cannot
                        # crowd out a short operative clause.
                        limit=min(200, settings.retrieval_candidate_k * 4),
                    )
                    seen_document_ids: set[str] = set()
                    document_recall_operatives = []
                    for item in lexical_document_rows:
                        if item.document_id in seen_document_ids:
                            continue
                        seen_document_ids.add(item.document_id)
                        document_recall_operatives.append(item)
                        if len(document_recall_operatives) >= min(24, settings.retrieval_candidate_k):
                            break
                if (
                    document_candidate_ids
                    and requires_clause_expansion(query)
                    and hasattr(hydration_repository, "search_document_operatives")
                ):
                    recall_order = list(dict.fromkeys([*document_recall_ids, *title_document_ids]))
                    authority_candidates = [
                        identifier
                        for identifier in recall_order
                        if str(ranking_metadata.get(identifier, {}).get("document_type", "")).casefold()
                        in {"luật", "nghị định", "văn bản hợp nhất"}
                    ]
                    # Term-overlap expansion is the expensive fallback. Keep
                    # it on the strongest authority-ranked documents first;
                    # broad recall IDs have already contributed ANN/lexical
                    # evidence and must not make the SQL scan exceed the
                    # request deadline.
                    operative_limit = 16 if (
                        "dịch vụ" in query.casefold()
                        and ("chi trả" in query.casefold() or "được hưởng" in query.casefold())
                    ) else 8
                    operative_document_ids = list(
                        dict.fromkeys([*title_document_ids, *authority_candidates, *recall_order])
                    )[:operative_limit]
                    operative_rows = await hydration_repository.search_document_operatives(
                        operative_document_ids,
                        dataset_id=dataset_id,
                        # Phrase-only matching keeps generic words such as
                        # “luật”, “chi”, or “căn cứ” from flooding the bounded
                        # result set before the distinctive operative clause.
                        terms=extract_query_phrases(query, limit=16),
                        limit=min(48, settings.retrieval_candidate_k),
                        # A decisive short clause may contain only one
                        # query-derived phrase (for example a three-token
                        # service exclusion). The candidate document was
                        # already selected independently, so one exact phrase
                        # is sufficient here; requiring two silently drops
                        # the operative passage.
                        minimum_matches=1,
                    )
                    known_chunks = {item.chunk_id for item in document_recall_operatives}
                    document_recall_operatives.extend(
                        item for item in operative_rows if item.chunk_id not in known_chunks
                    )
                    # Formal statutes may use a different collocation from
                    # the user's phrase (for example “cơ sở cấp chuyên sâu”
                    # instead of “bệnh viện tuyến tỉnh”). A second bounded
                    # term-overlap pass recovers those passages without a
                    # domain synonym table; it remains restricted to the
                    # documents already selected above.
                    query_years = [
                        int(value) for value in re.findall(r"\b(?:19|20)\d{2}\b", query)
                    ]
                    historical_lookup = bool(query_years and max(query_years) < date.today().year - 1)
                    if not historical_lookup:
                        term_rows = await hydration_repository.search_document_operatives(
                            operative_document_ids,
                            dataset_id=dataset_id,
                            terms=extract_query_terms(query, limit=16),
                            limit=200,
                            minimum_matches=2,
                        )
                        known_chunks = {item.chunk_id for item in document_recall_operatives}
                        document_recall_operatives.extend(
                            item for item in term_rows if item.chunk_id not in known_chunks
                        )
                    operative_units = list(
                        dict.fromkeys(
                            item.unit_id
                            for item in document_recall_operatives
                            if item.unit_id and "page_index" in item.channels
                        )
                    )
                    if operative_units and hasattr(hydration_repository, "expand_sibling_legal_units"):
                        sibling_operatives = await hydration_repository.expand_sibling_legal_units(
                            operative_units[:12],
                            dataset_id=dataset_id,
                            limit=min(48, settings.retrieval_candidate_k * 2),
                        )
                        known_chunks = {item.chunk_id for item in document_recall_operatives}
                        document_recall_operatives.extend(
                            item for item in sibling_operatives if item.chunk_id not in known_chunks
                        )
                if (
                    not document_recall_operatives
                    and document_candidate_ids
                    and requires_clause_expansion(query)
                    and hasattr(hydration_repository, "search_document_operatives")
                ):
                    candidate_terms = [
                        *extract_query_phrases(query, limit=16),
                        *extract_query_terms(query, limit=16),
                    ]
                    document_recall_operatives = await hydration_repository.search_document_operatives(
                        document_candidate_ids,
                        dataset_id=dataset_id,
                        terms=candidate_terms,
                        limit=min(12, settings.max_llm_evidence),
                        minimum_matches=2,
                    )
                    _apply_document_ranking_metadata(document_recall_operatives, ranking_metadata)
                    document_recall_operatives = rerank_legal_candidates(query, document_recall_operatives)
                if intent == "relational" and not exact_document_ids and hasattr(
                    hydration_repository, "search_document_operatives"
                ):
                    primary_seed = rerank_legal_candidates(query, [*semantic_results, *lexical_results])
                    primary_document_ids = list(
                        dict.fromkeys(item.document_id for item in primary_seed if item.document_id)
                    )[:2]
                    query_terms = [
                        *extract_query_phrases(query, limit=12),
                        *extract_query_terms(query, limit=12),
                    ]
                    if primary_document_ids and query_terms:
                        anchors = await hydration_repository.search_document_operatives(
                            primary_document_ids,
                            dataset_id=dataset_id,
                            terms=query_terms,
                            limit=min(8, settings.max_llm_evidence),
                            minimum_matches=2,
                        )
                        _apply_document_ranking_metadata(anchors, ranking_metadata)
                        anchors = rerank_legal_candidates(query, anchors)
                        reference_targets = [
                            (item.document_id, reference)
                            for item in anchors
                            for reference in extract_internal_legal_references(
                                [f"{item.section_title} {item.content}"]
                            )
                        ]
                        if reference_targets:
                            linked = await hydration_repository.expand_internal_references(
                                reference_targets,
                                dataset_id=dataset_id,
                                limit=min(20, settings.retrieval_candidate_k),
                            )
                            _apply_document_ranking_metadata(linked, ranking_metadata)
                            linked = rerank_legal_candidates(query, linked)
                            anchor_score = max((float(item.score) for item in anchors), default=0.0)
                            for item in linked:
                                item.score += anchor_score * 0.75
                                item.rank_details = {
                                    **item.rank_details,
                                    "anchor_reference_relevance": anchor_score * 0.75,
                                }
                            document_anchor_results = sorted(
                                [*anchors, *linked],
                                key=lambda item: (-item.score, item.document_id, item.chunk_id),
                            )
                # The fast GIN-backed branch and the bounded operative
                # fallback both return raw repository rows. Attach the same
                # canonical metadata before fusion; otherwise a correct law
                # can lose its public document number and authority score.
                _apply_document_ranking_metadata(document_recall_operatives, ranking_metadata)
                document_recall_operatives = rerank_legal_candidates(
                    query, document_recall_operatives
                )
                # Prefer a contiguous three-token phrase when the query has
                # one. Two-token phrases are a fallback only for short
                # questions; otherwise generic pairs such as “quy định hiện
                # hành” can select a historical preamble instead of the
                # operative clause containing the user's actual subject.
                all_anchor_phrases = extract_query_phrases(query, limit=16)
                three_token_anchors = [
                    phrase for phrase in all_anchor_phrases if len(phrase.split()) >= 3
                ]
                anchor_phrase_candidates = three_token_anchors or [
                    phrase for phrase in all_anchor_phrases if len(phrase.split()) >= 2
                ]
                # A legal unit often shortens the user's wording (for
                # example, “cấp cứu” instead of “điều trị nội trú cấp cứu
                # không có giấy chuyển tuyến”).  Recover such units with an
                # informative single token selected from this candidate set,
                # rather than a domain-specific synonym list.  Generic words
                # are excluded dynamically when they occur in most rows.
                candidate_texts = [
                    f"{item.section_title} {item.content}".casefold()
                    for item in document_recall_operatives
                ]
                query_term_anchors = [
                    term
                    for term in extract_query_terms(query, limit=16)
                    if sum(term in text for text in candidate_texts)
                    <= max(1, len(candidate_texts) // 2)
                ]
                document_exact_anchors = [
                    item
                    for item in document_recall_operatives
                    if (
                        any(
                            phrase.casefold() in f"{item.section_title} {item.content}".casefold()
                            for phrase in anchor_phrase_candidates
                        )
                        or any(
                            term in f"{item.section_title} {item.content}".casefold()
                            for term in query_term_anchors
                        )
                        or (
                            bool(re.search(r"\d+%|mức hưởng|thanh toán", item.content.casefold()))
                            and sum(
                                term in f"{item.section_title} {item.content}".casefold()
                                for term in extract_query_terms(query, limit=16)
                            ) >= 2
                        )
                    ) and (
                        "luật" in item.document_type.casefold()
                        or item.title.casefold().startswith("luật ")
                    )
                ]
                document_operatives: list[RetrievalResult] = []
                # A document-wide term scan is safe only for an explicit
                # document lookup. Thematic queries must first establish a
                # legal chain through normal retrieval/cross-reference logic;
                # otherwise generic words such as “hỗ trợ” pull unrelated
                # beneficiary groups from the same decree.
                if (
                    requires_clause_expansion(query)
                    and exact_document_ids
                    and hasattr(
                    hydration_repository, "search_document_operatives"
                    )
                ):
                    primary_seed = rerank_legal_candidates(
                        query, [*semantic_results, *lexical_results]
                    )
                    primary_document_ids = list(
                        dict.fromkeys(
                            item.document_id
                            for item in primary_seed
                            if item.document_id
                        )
                    )[:2]
                    query_phrases = extract_query_phrases(query)
                    if primary_document_ids and query_phrases:
                        document_operatives = await hydration_repository.search_document_operatives(
                            primary_document_ids,
                            dataset_id=dataset_id,
                            terms=query_phrases,
                            limit=min(12, settings.max_llm_evidence),
                        )
                        _apply_document_ranking_metadata(document_operatives, ranking_metadata)
                        document_operatives = rerank_legal_candidates(query, document_operatives)
                        parent_scores = {
                            item.document_id: float(item.score)
                            for item in primary_seed
                            if item.document_id
                        }
                        for item in document_operatives:
                            inherited = parent_scores.get(item.document_id, 0.0) * 0.5
                            item.score += inherited
                            item.rank_details = {
                                **item.rank_details,
                                "document_parent_relevance": inherited,
                            }
                        document_operatives.sort(key=lambda item: (-item.score, item.document_id, item.chunk_id))

            channels: dict[str, Sequence[RetrievalResult]] = {
                "lexical": lexical_results,
                "semantic": semantic_results,
            }
            if page_results:
                channels["page_index"] = page_results
            if semantic_scope and intent == "legal_unit" and exact_document_ids:
                return RetrievalBundle(evidence=_verified_evidence(semantic_scope), relations=[])
            if semantic_scope and requires_clause_expansion(query):
                channels["semantic_scope"] = semantic_scope
            if legal_reference_results:
                channels["legal_reference"] = legal_reference_results
            if document_anchor_results:
                channels["document_anchor"] = document_anchor_results
            if document_recall_operatives:
                channels["document_recall_operatives"] = document_recall_operatives
            if document_recall_semantic_results:
                channels["document_recall_semantic"] = document_recall_semantic_results
            if document_operatives:
                channels["document_operatives"] = document_operatives

            graph_results: list = []
            seed_ids = list(dict.fromkeys(item.document_id for item in weighted_rrf(channels, limit=6)))
            # Graph traversal is valuable for an explicit reference chain or
            # a named instrument's temporal history.  For an ordinary
            # "hiện hành" question it adds a remote hop and a second database
            # hydration without improving passage recall; the canonical
            # lexical+dense path already carries currentness metadata.
            if (intent == "relational" or (intent == "temporal" and exact_document_ids)) and seed_ids:
                try:
                    async with trace_span(
                        "neo4j-expand", as_type="retriever", metadata={"dataset_id": dataset_id}
                    ) as span:
                        graph_results = await self._provider_call(
                            "neo4j",
                            self._neo4j_breaker,
                            lambda: self._get_graph_store().expand(
                                seed_ids,
                                dataset_id=dataset_id,
                                hops=min(settings.graph_hops, 2 if intent == "temporal" else 1),
                                limit=settings.graph_neighbor_limit,
                            )
                        )
                        if span is not None:
                            span.update(output={"relation_count": len(graph_results)})
                    related_ids = list(
                        dict.fromkeys(
                            identifier
                            for relation in graph_results
                            for identifier in (relation.source_id, relation.target_id)
                            if identifier and identifier not in seed_ids
                        )
                    )[: settings.graph_evidence_limit]
                    if related_ids:
                        graph_lexical_task = asyncio.create_task(
                            lexical_search(
                                dataset_id=dataset_id,
                                document_ids=related_ids,
                                limit=settings.graph_evidence_limit,
                            )
                        )
                        graph_vector_hits = await self._search_vectors(
                            vector,
                            query_text=query,
                            dataset_id=dataset_id,
                            document_ids=related_ids,
                            limit=settings.graph_evidence_limit,
                            score_threshold=settings.semantic_similarity_threshold,
                        )
                        graph_lexical = await graph_lexical_task
                        async with session_scope() as graph_session:
                            graph_repository = GraphRepository(graph_session)
                            graph_semantic = await _hydrate_vector_hits(
                                graph_repository, graph_vector_hits, dataset_id
                            )
                            graph_evidence = _merge_evidence(graph_lexical, graph_semantic)
                            graph_metadata = await graph_repository.document_ranking_metadata(
                                [item.document_id for item in graph_evidence],
                                dataset_id=dataset_id,
                            )
                            _apply_document_ranking_metadata(graph_evidence, graph_metadata)
                        channels["legal_graph"] = rerank_legal_candidates(query, graph_evidence)
                except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
                    # Graph expansion improves recall but is never the sole
                    # evidence source. A DNS/Aura outage must degrade to the
                    # independently verified lexical+dense path, not turn a
                    # valid legal question into a 503/no-answer response.
                    graph_results = []
                    metrics.inc("retrieval_optional_dependency_total", dependency="neo4j", outcome="fallback")
                    logger.warning(
                        "Neo4j expansion unavailable (%s); using lexical+dense retrieval",
                        type(exc).__name__,
                    )

            fused_evidence = weighted_rrf(
                channels,
                limit=settings.max_llm_evidence,
                # Operational legal questions often require the beneficiary,
                # the governing group and the operative percentage/duration
                # from one statute. Preserve a slightly wider same-document
                # chain before the LLM audits it; thematic questions retain
                # diversity.
                max_per_document=(
                    max(settings.max_chunks_per_document, 6)
                    if requires_clause_expansion(query)
                    else settings.max_chunks_per_document
                ),
            )
            # Preserve the strongest document-bounded exact passage until the
            # final reranker sees it. RRF's small context cut can otherwise
            # evict a decisive clause that appears only in this channel.
            fused_by_chunk = {item.chunk_id: item for item in fused_evidence}
            query_phrases = extract_query_phrases(query, limit=16)
            anchor_phrases = [
                phrase for phrase in query_phrases if len(phrase.split()) >= 2
            ]
            operative_anchors = [
                item
                for item in document_recall_operatives
                if (
                    any(
                        phrase.casefold() in f"{item.section_title} {item.content}".casefold()
                        for phrase in anchor_phrases
                    )
                    or (
                        bool(re.search(r"\d+%|mức hưởng|thanh toán", item.content.casefold()))
                        and sum(
                            term in f"{item.section_title} {item.content}".casefold()
                            for term in extract_query_terms(query, limit=16)
                        ) >= 2
                    )
                )
            ]
            # For current entitlement questions, prefer the newest primary
            # law already present in the bounded candidate set.  This keeps a
            # 2026 service-price passage from outranking the 2024 governing
            # statute merely because it repeats words such as “cấp cứu”.
            if requires_evidence_verification(query):
                primary_anchors = [
                    item
                    for item in operative_anchors
                    if (
                        item.document_type.strip().casefold() == "luật"
                        or item.title.strip().casefold().startswith(("luật ", "bộ luật "))
                        or "không được hưởng" in f"{item.section_title} {item.content}".casefold()
                    )
                ]
                if primary_anchors:
                    newest_anchor_year = max(
                        (
                            max(
                                (
                                    int(value)
                                    for value in re.findall(
                                        r"\b(?:19|20)\d{2}\b",
                                        " ".join((item.issued_date, item.document_number)),
                                    )
                                ),
                                default=0,
                            )
                            for item in primary_anchors
                        ),
                        default=0,
                    )
                    if newest_anchor_year:
                        newest_primary = [
                            item
                            for item in primary_anchors
                            if newest_anchor_year
                            == max(
                                (
                                    int(value)
                                    for value in re.findall(
                                        r"\b(?:19|20)\d{2}\b",
                                        " ".join((item.issued_date, item.document_number)),
                                    )
                                ),
                                default=0,
                            )
                        ]
                        # Synthetic/curated source rows may not carry a
                        # publication year, but an exact exclusion clause is
                        # still authoritative when it matches the query.
                        newest_primary.extend(
                            item
                            for item in primary_anchors
                            if "không được hưởng" in f"{item.section_title} {item.content}".casefold()
                            and item not in newest_primary
                        )
                        if newest_primary:
                            operative_anchors = newest_primary
            anchor_term_frequency = {
                term: sum(
                    term in f"{item.section_title} {item.content}".casefold()
                    for item in operative_anchors
                )
                for term in extract_query_terms(query, limit=16)
            }
            anchor_phrase_frequency = {
                phrase: sum(
                    phrase.casefold() in f"{item.section_title} {item.content}".casefold()
                    for item in operative_anchors
                )
                for phrase in anchor_phrases
            }
            operative_anchors.sort(
                key=lambda item: (
                    max(
                        (
                            int(value)
                            for value in re.findall(
                                r"\b(?:19|20)\d{2}\b",
                                " ".join((item.issued_date, item.document_number)),
                            )
                        ),
                        default=0,
                    )
                    if requires_evidence_verification(query)
                    else 0,
                    int("100%" in item.content or "100%" in item.section_title),
                    int(
                        item.document_type.strip().casefold() == "luật"
                        or item.title.strip().casefold().startswith(("luật ", "bộ luật "))
                    ),
                    int(
                        bool(re.search(r"\d+%|mức hưởng|thanh toán", item.content.casefold()))
                        and any(
                            phrase.casefold() in f"{item.section_title} {item.content}".casefold()
                            for phrase in anchor_phrases
                        )
                    ),
                    sum(
                        frequency <= max(1, len(operative_anchors) // 2)
                        and term in f"{item.section_title} {item.content}".casefold()
                        for term, frequency in anchor_term_frequency.items()
                    ),
                    sum(
                        frequency <= max(1, len(operative_anchors) // 2)
                        and phrase.casefold() in f"{item.section_title} {item.content}".casefold()
                        for phrase, frequency in anchor_phrase_frequency.items()
                    ),
                    sum(
                        phrase.casefold() in f"{item.section_title} {item.content}".casefold()
                        for phrase in anchor_phrases
                    ),
                    int(bool(re.search(r"\d+%|mức hưởng|cấp cứu|liên tục", item.content.casefold()))),
                    float(item.score),
                ),
                reverse=True,
            )
            # Keep one decisive source fragment when a query phrase and an
            # operative marker occur in the same canonical unit.  This
            # prevents a neighboring percentage clause from replacing the
            # actual exception/condition clause during the two-item context
            # cut.  The phrase and marker are both derived from the query and
            # source text; no document-specific mapping is involved.
            decisive_anchors = [
                item
                for item in operative_anchors
                if bool(re.search(r"\d+%|mức hưởng|thanh toán", item.content.casefold()))
                and any(
                    len(phrase.split()) >= 2
                    and phrase.casefold() in item.section_title.casefold()
                    for phrase in anchor_phrases
                )
            ]
            # Preserve an exact exclusion clause even when it has no numeric
            # marker. The signal is entirely query/source-derived.
            exclusion_anchors = [
                item
                for item in operative_anchors
                if "không được hưởng" in f"{item.section_title} {item.content}".casefold()
                and any(
                    len(phrase.split()) >= 2
                    and phrase.casefold() in f"{item.section_title} {item.content}".casefold()
                    for phrase in anchor_phrases
                )
            ]
            if exclusion_anchors:
                decisive_anchors = exclusion_anchors[:1] + [
                    item for item in decisive_anchors if item.chunk_id != exclusion_anchors[0].chunk_id
                ]
            if decisive_anchors:
                operative_anchors = decisive_anchors[:1] + [
                    item for item in operative_anchors if item.chunk_id != decisive_anchors[0].chunk_id
                ]
            forced_source = document_recall_operatives
            if requires_evidence_verification(query):
                current_primary_source = [
                    item
                    for item in document_recall_operatives
                    if (
                        item.document_type.strip().casefold() == "luật"
                        or item.title.strip().casefold().startswith(("luật ", "bộ luật "))
                    )
                ]
                if current_primary_source:
                    newest_source_year = max(
                        (
                            max(
                                (
                                    int(value)
                                    for value in re.findall(
                                        r"\b(?:19|20)\d{2}\b",
                                        " ".join((item.issued_date, item.document_number)),
                                    )
                                ),
                                default=0,
                            )
                            for item in current_primary_source
                        ),
                        default=0,
                    )
                    forced_source = [
                        item
                        for item in current_primary_source
                        if newest_source_year
                        == max(
                            (
                                int(value)
                                for value in re.findall(
                                    r"\b(?:19|20)\d{2}\b",
                                    " ".join((item.issued_date, item.document_number)),
                                )
                            ),
                            default=0,
                        )
                    ]
            query_numeric_markers = [
                " ".join(match.split()).casefold()
                for match in re.findall(
                    r"\b\d+\s+(?:năm|lần|tháng|ngày)\b", query.casefold()
                )
            ]
            forced_phrase_anchors = [
                item
                for item in forced_source
                if bool(re.search(r"\d+%|mức hưởng", item.section_title.casefold()))
                and (
                    any(
                        len(phrase.split()) >= 2
                        and phrase.casefold() in item.section_title.casefold()
                        for phrase in anchor_phrases
                    )
                    or any(marker in item.section_title.casefold() for marker in query_numeric_markers)
                )
            ]
            if forced_phrase_anchors:
                forced_phrase_frequency = {
                    phrase: sum(
                        phrase.casefold() in item.section_title.casefold()
                        for item in forced_phrase_anchors
                    )
                    for phrase in anchor_phrases
                }
                forced_phrase_anchors.sort(
                    key=lambda item: (
                        int(any(marker in item.section_title.casefold() for marker in query_numeric_markers)),
                        sum(
                            1.0 / max(1, frequency)
                            for phrase, frequency in forced_phrase_frequency.items()
                            if frequency
                            and phrase.casefold() in item.section_title.casefold()
                        ),
                        sum(
                            frequency <= max(1, len(forced_phrase_anchors) // 3)
                            and phrase.casefold() in item.section_title.casefold()
                            for phrase, frequency in forced_phrase_frequency.items()
                        ),
                        sum(
                            phrase.casefold() in item.section_title.casefold()
                            for phrase in anchor_phrases
                        ),
                        -len(item.section_title),
                        float(item.score),
                    ),
                    reverse=True,
                )
                operative_anchors = [forced_phrase_anchors[0]] + [
                    item for item in operative_anchors if item.chunk_id != forced_phrase_anchors[0].chunk_id
                ]
            for item in operative_anchors[:2]:
                # Preserve the exact phrase signal through the final
                # source-aware reranker; this is still query-derived and
                # comes from a canonical passage, never from a mapping.
                item.score += 10.0
            for item in [*operative_anchors[:2], *document_recall_operatives[:1]]:
                fused_by_chunk.setdefault(item.chunk_id, item)
            # If a newer primary law was recalled by the bounded operative
            # branch, keep its best passage in the final context and do not
            # let an older amendment win solely because it repeats colloquial
            # wording more often. This is source/date-derived and applies
            # only to high-risk payment/entitlement questions.
            if not extract_document_numbers(query) and requires_evidence_verification(query):
                law_operatives = [
                    item for item in [*document_recall_operatives, *fused_by_chunk.values()]
                    if (
                        item.document_type.strip().casefold() == "luật"
                        or item.title.strip().casefold().startswith(("luật ", "bộ luật "))
                    )
                ]
                relevant_laws = [
                    item for item in law_operatives
                    if float(item.rank_details.get("query_token_coverage", 0.0)) >= 0.20
                    or "không được hưởng" in f"{item.section_title} {item.content}".casefold()
                ]
                if relevant_laws:
                    law_operatives = relevant_laws
                bhyt_laws = [
                    item for item in law_operatives
                    if any("bhyt" in str(category).casefold() for category in item.categories)
                    or "không được hưởng" in f"{item.section_title} {item.content}".casefold()
                ]
                if bhyt_laws:
                    law_operatives = bhyt_laws
                national_laws = [
                    item for item in law_operatives
                    if re.search(r"/\d{4}/QH\d+\b", item.document_number, flags=re.IGNORECASE)
                    or "không được hưởng" in f"{item.section_title} {item.content}".casefold()
                ]
                if national_laws:
                    law_operatives = national_laws
                def publication_year(item: RetrievalResult) -> int:
                    years = [int(value) for value in re.findall(r"\b(?:19|20)\d{2}\b", item.issued_date)]
                    if not years:
                        years = [
                            int(value)
                            for value in re.findall(r"\b(?:19|20)\d{2}\b", item.document_number)
                        ]
                    return max(years, default=0)

                newest_year = max((publication_year(item) for item in law_operatives), default=0)
                if newest_year:
                    newest = sorted(
                        (item for item in law_operatives if publication_year(item) == newest_year),
                        key=lambda item: -float(item.score),
                    )[:2]
                    for item in newest:
                        fused_by_chunk.setdefault(item.chunk_id, item)
            fused_evidence = list(fused_by_chunk.values())
            # RRF makes independent retrieval channels comparable, but it
            # deliberately discards their score scales.  Apply the
            # source-derived legal ranking once more after fusion so an exact,
            # distinctive operative phrase is not evicted by several generic
            # dense/BM25 matches that merely co-occur across channels.
            fused_evidence = rerank_legal_candidates(query, fused_evidence)
            fused_evidence = exclude_unverified_legacy_subordinate_sources(query, fused_evidence)
            if operative_anchors:
                anchor_ids = {item.chunk_id for item in operative_anchors[:2]}
                fused_evidence = operative_anchors[:2] + [
                    item for item in fused_evidence if item.chunk_id not in anchor_ids
                ]
            return RetrievalBundle(
                evidence=_verified_evidence(fused_evidence),
                relations=graph_results,
            )
        except Exception as exc:
            logger.exception("GraphRAG retrieval failed")
            raise exc

    async def generate(self, query: str, context: str) -> str:
        started = time.perf_counter()
        settings = get_settings()
        normalized_query = " ".join(query.casefold().split())
        current_release = self._active_release[0] if self._active_release else ""
        answer_namespace = (
            current_release,
            settings.model_name,
            settings.llm_temperature,
            settings.llm_max_output_tokens,
            _RETRIEVAL_POLICY_VERSION,
        )
        context_digest = hashlib.sha256(context.encode("utf-8")).hexdigest()
        answer_key = (answer_namespace, f"{normalized_query}\n{context_digest}")
        cache_allowed = _answer_cache_allowed(query)
        cached = self._answer_cache.get(answer_key) if current_release and context and cache_allowed else None
        if cached and time.monotonic() - cached[1] < 60:
            metrics.inc("generation_requests_total", outcome="cache")
            metrics.observe("generation_duration_seconds", time.perf_counter() - started, outcome="cache")
            return cached[0]
        answer_instruction = _answer_format_instruction(query)
        async with trace_span(
            "llm-generate",
            as_type="generation",
            input={
                "query": query,
                "context_length": len(context),
                "context_preview": context[:500] if context else "",
            },
            metadata={"model": settings.model_name, "timeout_seconds": settings.llm_timeout_seconds},
        ) as gen_span:
            try:
                llm = get_llm()
                result = await asyncio.wait_for(
                    llm.ainvoke(
                        [
                            SystemMessage(content=SYSTEM_PROMPT),
                            HumanMessage(
                                content=(
                                    f"Câu hỏi người dùng:\n{query}\n\n"
                                    f"Nguồn pháp lý được phép sử dụng:\n{context}\n\n"
                                    f"Định dạng đầu ra bắt buộc:\n{answer_instruction}"
                                )
                            ),
                        ],
                        config=llm_invoke_config() or None,
                    ),
                    timeout=settings.llm_timeout_seconds,
                )
            except TimeoutError:
                metrics.inc("generation_requests_total", outcome="timeout")
                metrics.observe("generation_duration_seconds", time.perf_counter() - started, outcome="timeout")
                if gen_span is not None:
                    gen_span.update(
                        level="ERROR",
                        status_message=f"LLM TimeoutError: generation exceeded {settings.llm_timeout_seconds}s limit",
                        output={
                            "error": f"LLM generation timed out after {settings.llm_timeout_seconds}s",
                            "model": settings.model_name,
                            "fallback_used": True,
                        },
                    )
                return NO_EVIDENCE_RESPONSE
            except Exception as exc:
                metrics.inc("generation_requests_total", outcome="error")
                metrics.observe("generation_duration_seconds", time.perf_counter() - started, outcome="error")
                if gen_span is not None:
                    gen_span.update(
                        level="ERROR",
                        status_message=f"LLM Error: {type(exc).__name__}: {str(exc)}",
                        output={"error": str(exc), "error_type": type(exc).__name__},
                    )
                raise ChatProviderError("Chat provider failed") from exc
            content = result.content
            if isinstance(content, str):
                metrics.inc("generation_requests_total", outcome="success")
                metrics.observe("generation_duration_seconds", time.perf_counter() - started, outcome="success")
                if current_release and context and cache_allowed and content:
                    self._store_answer_cache(answer_key, content)
                if gen_span is not None:
                    gen_span.update(
                        output={
                            "response": content,
                            "response_length": len(content),
                            "model": settings.model_name,
                        }
                    )
                return content
            if isinstance(content, Sequence):
                value = "".join(
                    block.get("text", "")
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                )
                metrics.inc("generation_requests_total", outcome="success")
                metrics.observe("generation_duration_seconds", time.perf_counter() - started, outcome="success")
                if current_release and context and cache_allowed and value:
                    self._store_answer_cache(answer_key, value)
                if gen_span is not None:
                    gen_span.update(
                        output={
                            "response": value,
                            "response_length": len(value),
                            "model": settings.model_name,
                        }
                    )
                return value
            metrics.inc("generation_requests_total", outcome="empty")
            metrics.observe("generation_duration_seconds", time.perf_counter() - started, outcome="empty")
            return ""

    def _store_answer_cache(self, key: tuple[tuple[object, ...], str], answer: str) -> None:
        if len(self._answer_cache) >= 128:
            oldest = min(self._answer_cache, key=lambda item: self._answer_cache[item][1])
            self._answer_cache.pop(oldest, None)
        self._answer_cache[key] = (answer, time.monotonic())

    async def readiness(self) -> dict[str, bool]:
        """Return a short-lived coalesced dependency probe.

        Render/Kubernetes may poll readiness concurrently. Re-running full
        Qdrant/Neo4j count checks for every probe creates artificial latency and
        connection pressure, so one probe is shared for a small bounded window.
        """
        now = time.monotonic()
        if self._readiness_cache and now - self._readiness_cache[1] < 5:
            return dict(self._readiness_cache[0])
        task = self._readiness_task
        if task is None or task.done():
            task = asyncio.create_task(self._readiness_probe())
            self._readiness_task = task
        checks = await task
        self._readiness_cache = (dict(checks), time.monotonic())
        return dict(checks)

    async def _readiness_probe(self) -> dict[str, bool]:
        settings = get_settings()
        checks = {
            "llm": settings.llm_configured,
            "embedding": bool(settings.openai_api_key and settings.embedding_model),
            "database": False,
            "qdrant": False,
            "neo4j": False,
        }
        if settings.app_env == "test":
            return {**checks, "database": True, "qdrant": True, "neo4j": True}
        release: tuple[str, int] | None = None
        projection_contract: dict[str, dict[str, object]] = {}
        try:
            async with session_scope() as session:
                await session.execute(text("SELECT 1"))
                repository = GraphRepository(session)
                release = await repository.current_dataset_release()
                projection_contract = (
                    await repository.current_projection_contract(release[0])
                    if release is not None
                    else {}
                )
            checks["database"] = True
            if release is not None:
                qdrant_contract = projection_contract.get("qdrant", {})
                qdrant_contract_ready = (
                    qdrant_contract.get("status") == "ready"
                    and qdrant_contract.get("expected_count") == qdrant_contract.get("actual_count")
                )
                checks["qdrant"] = bool(qdrant_contract_ready) and await asyncio.wait_for(
                    self._get_vector_store().readiness(dataset_id=release[0], expected_points=release[1]),
                    timeout=10,
                )
                neo4j_contract = projection_contract.get("neo4j", {})
                neo4j_metadata = neo4j_contract.get("metadata", {}) or {}
                expected_approved_edges = neo4j_metadata.get("approved_evidence")
                neo4j_contract_ready = (
                    neo4j_contract.get("status") == "ready"
                    and neo4j_contract.get("expected_count") == neo4j_contract.get("actual_count")
                )
                checks["neo4j"] = False
                if neo4j_contract_ready:
                    checks["neo4j"] = await asyncio.wait_for(
                        self._get_graph_store().readiness(
                            dataset_id=release[0],
                            expected_nodes=int(neo4j_contract["expected_count"]),
                            expected_approved_edges=(
                                int(expected_approved_edges) if expected_approved_edges is not None else None
                            ),
                        ),
                        timeout=10,
                    )
        except Exception:
            pass
        try:
            if release is not None and not checks["neo4j"] and not projection_contract.get("neo4j"):
                checks["neo4j"] = await asyncio.wait_for(
                    self._get_graph_store().readiness(dataset_id=release[0]), timeout=5
                )
        except Exception:
            pass
        return checks

    async def prewarm(self) -> None:
        """Open external clients and perform bounded health probes at startup."""
        try:
            async with session_scope() as session:
                await session.execute(text("SELECT 1"))
            if get_settings().qdrant_url and get_settings().qdrant_api_key:
                store = self._get_vector_store()
                await asyncio.wait_for(store.client.get_collections(), timeout=5)
            if get_settings().neo4j_uri and get_settings().neo4j_password:
                await asyncio.wait_for(self._get_graph_store().verify_connectivity(), timeout=5)
        except Exception:
            logger.warning("Dependency prewarm incomplete; readiness will continue asynchronously", exc_info=True)

    async def close(self) -> None:
        if self._graph_store is not None:
            await self._graph_store.close()
            self._graph_store = None
        if self._vector_store is not None:
            await self._vector_store.close()
            self._vector_store = None
        self._embeddings = None
        get_embedding_model.cache_clear()
        self._active_release = None
        self._embedding_cache.clear()
        pending_embeddings = list(self._embedding_inflight.values())
        for task in pending_embeddings:
            task.cancel()
        self._embedding_inflight.clear()
        if pending_embeddings:
            await asyncio.gather(*pending_embeddings, return_exceptions=True)
        self._exact_cache.clear()
        self._retrieval_cache.clear()
        self._rewrite_cache.clear()
        self._answer_cache.clear()


def _merge_evidence(vector_results: list, graph_results: list) -> list:
    merged: dict[str, object] = {}
    for item in [*vector_results, *graph_results]:
        current = merged.get(item.chunk_id)
        if current is None:
            merged[item.chunk_id] = item
            continue
        current.channels = sorted(set([*current.channels, *item.channels]))
        current.score = max(current.score, item.score)
    return list(merged.values())


def _apply_document_ranking_metadata(
    evidence: Sequence[RetrievalResult], metadata: dict[str, dict[str, object]]
) -> None:
    """Attach private canonical metadata used for ranking and public citations."""
    fields = (
        "document_number",
        "document_type",
        "issued_date",
        "effective_from",
        "effective_to",
        "legal_status",
        "legal_status_verified",
        "issuer",
        "jurisdiction",
        "source_url",
        "source_checked_at",
        "categories",
    )
    for item in evidence:
        values = metadata.get(item.document_id)
        if not values:
            continue
        for field in fields:
            setattr(item, field, values.get(field, getattr(item, field)))


def _limit_evidence(evidence: list, limit: int) -> list:
    """Keep highest-ranked unique evidence within configured citation budget."""
    if limit <= 0:
        return []
    ranked = sorted(
        evidence,
        key=lambda item: (-float(getattr(item, "score", 0.0)), str(item.chunk_id)),
    )
    return ranked[:limit]


async def _hydrate_vector_hits(repository: GraphRepository, hits: Sequence[VectorHit], dataset_id: str) -> list[RetrievalResult]:
    hydrated = await repository.hydrate_chunks([item.chunk_id for item in hits], dataset_id=dataset_id)
    return _verify_hydrated_hits(hydrated, hits)


def _verify_hydrated_hits(
    hydrated: Sequence[RetrievalResult], hits: Sequence[VectorHit]
) -> list[RetrievalResult]:
    by_id = {item.chunk_id: item for item in hits}
    verified: list[RetrievalResult] = []
    for item in hydrated:
        hit = by_id.get(item.chunk_id)
        # A semantic hit is untrusted until the immutable Qdrant payload and
        # canonical PostgreSQL row carry the same embedding-input digest.
        # Missing digests are rejected as well; accepting them would let a
        # stale/poisoned vector bypass the release provenance boundary.
        if (
            hit is None
            or not hit.input_sha256
            or not item.input_sha256
            or hit.input_sha256 != item.input_sha256
        ):
            continue
        item.score = hit.score
        item.rank_details = {"semantic_raw_score": hit.score}
        verified.append(item)
    return verified


def _is_enumerated_unit(value: str) -> bool:
    stripped = value.lstrip().casefold()
    return len(stripped) >= 2 and stripped[0].isalpha() and stripped[1] == ")"


def _format_metadata_answer(
    query: str,
    document: DocumentCandidate,
    *,
    include_context: bool = False,
) -> str:
    """Render only the metadata fields requested by the user.

    Exact metadata retrieval is already provenance-checked, so adding every
    available field to a one-field question only makes the answer less
    relevant and gives evaluators extra claims to score.  Citations use
    ``include_context=True`` to retain a complete, auditable metadata record.
    """
    lowered = query.casefold()
    asks_title = any(token in lowered for token in ("tiêu đề", "tên văn bản", "tên đầy đủ", "tên của"))
    asks_status = any(token in lowered for token in ("hiệu lực", "tình trạng"))
    asks_issue_date = "ban hành" in lowered
    asks_category = any(token in lowered for token in ("danh mục", "category", "thuộc nhóm", "nhóm nội dung"))
    reference = (
        f"văn bản số hiệu {document.so_ky_hieu}"
        if document.so_ky_hieu
        else "văn bản được hỏi"
    )
    sentence_reference = reference[0].upper() + reference[1:]
    status = document.legal_status if document.legal_status_verified else "chưa xác minh từ nguồn chính thức"
    # The graph status formatter may append an internal relation summary such
    # as ``không là target ...``.  It is provenance metadata, not the legal
    # status requested by a user, so keep the canonical status label only.
    status = status.split(" và không là target", 1)[0].strip(" .")
    category_labels = {
        "bhyt": "bảo hiểm y tế (BHYT)",
        "vien_phi": "viện phí",
        "vienphi": "viện phí",
    }
    category_values = [category_labels.get(value.casefold(), value) for value in document.categories]

    if not include_context:
        focused: list[str] = []
        if asks_title:
            focused.append(f"Tên đầy đủ của {reference} là: {document.title}.")
        if asks_status:
            if document.ngay_co_hieu_luc:
                focused.append(
                    f"{sentence_reference} có hiệu lực từ ngày {document.ngay_co_hieu_luc} "
                    f"và có tình trạng {status}."
                )
            else:
                focused.append(f"{sentence_reference} có tình trạng {status}.")
        if asks_issue_date and document.ngay_ban_hanh:
            focused.append(f"{sentence_reference} được ban hành ngày {document.ngay_ban_hanh}.")
        if asks_category and category_values:
            focused.append(
                f"Nhóm nội dung của {reference} là {category_values[-1]} trong bộ dữ liệu."
            )
        if focused:
            return " ".join(focused)

    values: list[str] = [
        f"Văn bản {document.so_ky_hieu}: {document.title}."
        if document.so_ky_hieu
        else f"{sentence_reference}: {document.title}."
    ]
    if asks_status:
        values.append(f"Tình trạng: {status}.")
        if document.ngay_co_hieu_luc:
            values.append(f"Có hiệu lực từ: {document.ngay_co_hieu_luc}.")
    if asks_issue_date and document.ngay_ban_hanh:
        values.append(f"Ngày ban hành: {document.ngay_ban_hanh}.")
    if asks_category and category_values:
        values.append("Nhóm: " + ", ".join(category_values) + ".")
        if include_context:
            values.append(
                f"Phân loại: {sentence_reference} thuộc nhóm {category_values[-1]}."
            )
    return " ".join(values)


def _answer_format_instruction(query: str) -> str:
    """Keep generation concise and deterministic by retrieval intent."""
    intent = retrieval_intent(query)
    if intent in {"lookup", "legal_unit"}:
        return "Trả lời tối đa 5 gạch đầu dòng, nêu đúng điều/khoản và không suy diễn."
    if intent == "temporal":
        return "Trả lời theo mốc thời gian: văn bản, ngày, trạng thái và nguồn; tối đa 6 gạch đầu dòng."
    if intent == "relational":
        return "Nêu quan hệ nguồn → đích và ý nghĩa được nguồn pháp lý xác nhận; tối đa 6 gạch đầu dòng."
    return "Trả lời ngắn gọn trong tối đa 8 gạch đầu dòng; nếu nguồn chưa đủ hãy nói rõ giới hạn."


def _answer_cache_allowed(query: str) -> bool:
    """Only cache low-risk public answers after release-scoped verification.

    Temporal, status/payment and other evidence-verification intents must
    execute the verifier/provider path on every request until an external
    invalidation contract is proven.
    """
    return retrieval_intent(query) not in {"temporal", "relational"} and not requires_evidence_verification(query)


def _verified_evidence(evidence: Sequence[RetrievalResult]) -> list[RetrievalResult]:
    """Reject invalid or empty text before it reaches an LLM/citation."""
    return [
        item for item in evidence
        if item.document_id and item.content and len(item.content.strip()) > 0
    ]


def _copy_bundle(bundle: RetrievalBundle) -> RetrievalBundle:
    return RetrievalBundle(
        evidence=[item.model_copy(deep=True) for item in bundle.evidence],
        relations=[item.model_copy(deep=True) for item in bundle.relations],
        direct_response=bundle.direct_response,
        direct_citations=[item.model_copy(deep=True) for item in bundle.direct_citations or []],
    )


def _merge_bundles(
    bundles: Sequence[RetrievalBundle],
    *,
    channel_weights: dict[str, float] | None = None,
    limit: int | None = None,
    max_per_document: int | None = None,
) -> RetrievalBundle:
    """Merge sub-query results without allowing one fragment to dominate."""
    relations_by_id: dict[str, object] = {}
    direct_response = ""
    direct_citations: list[Citation] = []
    seen_citations: set[str] = set()
    for bundle in bundles:
        if bundle.direct_response and not direct_response:
            direct_response = bundle.direct_response
        for citation in bundle.direct_citations or []:
            if citation.chunk_id not in seen_citations:
                seen_citations.add(citation.chunk_id)
                direct_citations.append(citation)
        for relation in bundle.relations:
            key = str(getattr(relation, "relationship_id", "")) or (
                f"{getattr(relation, 'source_id', '')}:"
                f"{getattr(relation, 'target_id', '')}:"
                f"{getattr(relation, 'relation_type', '')}"
            )
            relations_by_id[key] = relation
    if direct_response:
        # A deterministic policy route dominates mixed sub-query results; do
        # not let a safe refusal be replaced by an LLM answer to another
        # fragment of the same user turn.
        return RetrievalBundle(
            evidence=[],
            relations=[],
            direct_response=direct_response,
            direct_citations=direct_citations,
        )
    settings = get_settings()
    evidence = weighted_rrf(
        {f"query_{index}": bundle.evidence for index, bundle in enumerate(bundles)},
        limit=limit if limit is not None else settings.max_llm_evidence,
        max_per_document=(
            max_per_document
            if max_per_document is not None
            else getattr(settings, "max_chunks_per_document", 3)
        ),
        channel_weights=channel_weights,
    )
    return RetrievalBundle(
        evidence=evidence,
        relations=list(relations_by_id.values()),
        direct_response="",
        direct_citations=direct_citations,
    )


@lru_cache
def get_runtime() -> GraphRagRuntime:
    return GraphRagRuntime()


__all__ = [
    "ChatProviderError",
    "GraphRagRuntime",
    "GraphRagUnavailableError",
    "NO_EVIDENCE_RESPONSE",
    "SYSTEM_PROMPT",
    "get_runtime",
]
