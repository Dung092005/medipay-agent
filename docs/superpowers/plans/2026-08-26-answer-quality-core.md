# MediPay Answer-Quality Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Nâng độ chính xác của chatbot MediPay bằng hybrid retrieval, deterministic source-backed answers và claim-level evidence gates trên corpus hiện có.

**Architecture:** Giữ `GraphRagRuntime` làm orchestration boundary, nâng `QdrantVectorStore` thành adapter dense/BM25 có fallback, rồi đưa answer composition và claim verification vào các helper có thể test độc lập. Tavily giữ nguyên là pipeline enrichment offline và không tham gia request path.

**Tech Stack:** Python 3.11+, pytest/pytest-asyncio, qdrant-client, PostgreSQL FTS, LangGraph, Pydantic.

**Spec:** `docs/superpowers/specs/2026-08-26-answer-quality-core-design.md`

## Global Constraints

- Accuracy và provenance quan trọng hơn latency.
- BHYT/viện phí là phạm vi chính; BHXH chỉ trả lời khi evidence đủ.
- Không evidence hoặc evidence mâu thuẫn phải abstain.
- Không gọi Tavily trong runtime và không gọi cloud trong test.
- Không đổi model, dimensions, chunking, database schema hoặc active release.
- Không sửa `.env`, không dùng credential đã lộ và không commit.
- Worktree đang dirty: không reset/checkout/xóa file; chỉ dùng `apply_patch` cho file trong plan.
- Không thêm câu trả lời hard-code cho một document ID, một câu eval hoặc một quyền lợi cụ thể.

---

## File map

- `src/integrations/qdrant.py`: capability detection, dense/BM25 hybrid query, dense fallback và readiness cho named vectors.
- `src/services/chat.py`: truyền nguyên query text vào Qdrant adapter; không thêm answer business rules.
- `src/services/evidence_answer.py`: helper mới để chọn và render source-backed fragments, không chứa document-specific mappings.
- `src/agents/nodes/graphrag_nodes.py`: điều phối deterministic/LLM answer, sanitize output, audit và retain claim.
- `src/services/claims.py`: claim classification/serialization và numeric/status support primitives.
- `tests/test_integrations/test_qdrant.py`: contract test cho hybrid/fallback/readiness.
- `tests/test_services/test_evidence_answer.py`: deterministic composer tests.
- `tests/test_agents/test_graph.py`: guardrail, sanitizer, unsupported-claim và abstention integration tests.
- `tests/test_services/test_chat.py`: query text propagation và adaptive retrieval regression.
- `tests/fixtures/answer_quality_policy_cases.json`: policy/social/abstention cases không phụ thuộc production hashes.
- `tests/test_answer_quality_regression.py`: offline regression runner cho fixture và golden schema.

---

### Task 1: Qdrant dense/BM25 hybrid adapter

**Files:**
- Create: `tests/test_integrations/test_qdrant.py`
- Modify: `src/integrations/qdrant.py:1-209`

**Interfaces:**
- Consumes: Qdrant collection metadata, dense query vector, original query text and immutable `dataset_id`.
- Produces: `QdrantVectorStore._supports_hybrid_bm25() -> bool`.
- Produces: `search(vector, *, query_text, dataset_id, limit, document_ids=None, score_threshold=None) -> list[VectorHit]`.
- Produces: `search_many(vectors, *, query_texts=None, dataset_id, limit, document_ids=None, score_threshold=None) -> list[list[VectorHit]]`.

- [ ] **Step 1: Record the pre-task diff without modifying it**

Run:

```powershell
git -c "safe.directory=C:/Users/admin/OneDrive/Desktop/Al-20k/Group project" diff -- src/integrations/qdrant.py
```

Expected: existing user changes are visible and retained for comparison.

- [ ] **Step 2: Write failing hybrid, fallback and readiness tests**

Create `tests/test_integrations/test_qdrant.py` with fake clients and real `qdrant_client.models` objects:

```python
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from qdrant_client import models

from src.integrations.qdrant import QdrantVectorStore


def make_store(client, *, dimensions=3):
    store = object.__new__(QdrantVectorStore)
    store.collection = "medical_legal_active"
    store.dimensions = dimensions
    store.timeout = 5
    store.client = client
    store._hybrid_bm25 = None
    store._hybrid_bm25_checked_at = 0.0
    return store


@pytest.mark.asyncio
async def test_hybrid_search_uses_dense_and_bm25_prefetch_with_rrf():
    client = SimpleNamespace(query_points=AsyncMock(return_value=SimpleNamespace(points=[])))
    store = make_store(client)
    store._supports_hybrid_bm25 = AsyncMock(return_value=True)

    await store.search(
        [0.1, 0.2, 0.3], query_text="mức hưởng bảo hiểm y tế",
        dataset_id="release-1", limit=8, score_threshold=0.2,
    )

    call = client.query_points.await_args.kwargs
    assert len(call["prefetch"]) == 2
    assert call["prefetch"][0].using == "dense"
    assert call["prefetch"][1].using == "bm25"
    assert isinstance(call["query"], models.FusionQuery)
    assert "score_threshold" not in call


@pytest.mark.asyncio
async def test_hybrid_failure_falls_back_to_dense_and_disables_capability():
    client = SimpleNamespace(
        query_points=AsyncMock(
            side_effect=[RuntimeError("hybrid unavailable"), SimpleNamespace(points=[])]
        )
    )
    store = make_store(client)
    store._supports_hybrid_bm25 = AsyncMock(return_value=True)

    assert await store.search(
        [0.1, 0.2, 0.3], query_text="BHYT", dataset_id="release-1", limit=8
    ) == []
    assert client.query_points.await_count == 2
    assert store._hybrid_bm25 is False
    assert client.query_points.await_args.kwargs["query"] == [0.1, 0.2, 0.3]


@pytest.mark.asyncio
async def test_readiness_accepts_named_dense_vector_in_hybrid_release():
    client = SimpleNamespace(
        collection_exists=AsyncMock(return_value=True),
        get_collection=AsyncMock(return_value=SimpleNamespace(
            config=SimpleNamespace(
                params=SimpleNamespace(vectors={"dense": SimpleNamespace(size=3)}),
                metadata={"artifact_rows": 2},
            )
        )),
        count=AsyncMock(return_value=SimpleNamespace(count=2)),
    )
    store = make_store(client)
    assert await store.readiness(dataset_id="release-1", expected_points=2)


@pytest.mark.asyncio
async def test_hybrid_capability_is_rechecked_after_cache_ttl(monkeypatch):
    client = SimpleNamespace(get_collection=AsyncMock(return_value=SimpleNamespace(
        config=SimpleNamespace(params=SimpleNamespace(
            vectors={"dense": SimpleNamespace(size=3)},
            sparse_vectors={"bm25": SimpleNamespace()},
        ))
    )))
    store = make_store(client)
    store._hybrid_bm25 = False
    store._hybrid_bm25_checked_at = 100.0
    monkeypatch.setattr("src.integrations.qdrant.time.monotonic", lambda: 401.0)

    assert await store._supports_hybrid_bm25() is True
    client.get_collection.assert_awaited_once()
```

- [ ] **Step 3: Run the tests to prove the dense-only adapter fails the new contract**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_integrations\test_qdrant.py -q
```

Expected: FAIL because `search` does not require/use `query_text`, hybrid prefetch is absent, and readiness does not handle named dense vectors.

- [ ] **Step 4: Implement capability detection and hybrid search**

Update `QdrantVectorStore.__init__` and add the cached detector:

```python
self.client = AsyncQdrantClient(
    url=settings.qdrant_url,
    api_key=settings.qdrant_api_key,
    timeout=self.timeout,
    cloud_inference=True,
)
self._hybrid_bm25: bool | None = None
self._hybrid_bm25_checked_at = 0.0

async def _supports_hybrid_bm25(self) -> bool:
    now = time.monotonic()
    if self._hybrid_bm25 is not None and now - self._hybrid_bm25_checked_at < 300:
        return self._hybrid_bm25
    try:
        info = await self.client.get_collection(self.collection)
        vectors = info.config.params.vectors
        sparse = info.config.params.sparse_vectors or {}
        self._hybrid_bm25 = (
            isinstance(vectors, dict)
            and "dense" in vectors
            and "bm25" in sparse
        )
    except Exception:
        self._hybrid_bm25 = False
    self._hybrid_bm25_checked_at = now
    return self._hybrid_bm25
```

Import `time` at module scope. When a hybrid query fails, set both `_hybrid_bm25=False` and `_hybrid_bm25_checked_at=time.monotonic()` before falling back, so a transient cluster mismatch is retried after five minutes rather than cached forever.

Change `search` to require `query_text`. Build one shared release/answer-ready filter. In hybrid mode use:

```python
response = await self.client.query_points(
    collection_name=self.collection,
    prefetch=[
        models.Prefetch(
            query=[float(value) for value in vector],
            using="dense",
            limit=limit,
            score_threshold=score_threshold,
        ),
        models.Prefetch(
            query=models.Document(text=query_text, model="qdrant/bm25"),
            using="bm25",
            limit=limit,
        ),
    ],
    query=models.FusionQuery(fusion=models.Fusion.RRF),
    query_filter=query_filter,
    limit=limit,
    with_payload=["passage_id", "document_id", "unit_id", "input_sha256"],
    with_vectors=False,
    timeout=self.timeout,
)
```

Do not apply the dense cosine threshold to the fused RRF result. Catch a hybrid query exception, set `_hybrid_bm25=False`, then execute the existing dense query with the same filter.

In `search_many`, validate `len(query_texts) == len(vectors)` when texts are supplied. Hybrid mode uses bounded `asyncio.gather(self.search(...))`; dense mode preserves the existing batch/fan-out behavior.

In `readiness`, resolve the dense vector as:

```python
dense_vectors = vectors.get("dense") if isinstance(vectors, dict) else vectors
```

- [ ] **Step 5: Run adapter tests and the existing batch integration tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_integrations\test_qdrant.py tests\test_integrations\test_batch_paths.py -q
```

Expected: PASS; no network calls.

- [ ] **Step 6: Diff checkpoint (no commit)**

Run:

```powershell
git -c "safe.directory=C:/Users/admin/OneDrive/Desktop/Al-20k/Group project" diff -- src/integrations/qdrant.py tests/test_integrations/test_qdrant.py
```

Expected: only hybrid adapter and tests changed; no secret or environment file appears.

---

### Task 2: Propagate original query text through GraphRagRuntime

**Files:**
- Modify: `src/services/chat.py:500-525` and Qdrant call sites inside retrieval methods
- Modify: `tests/test_services/test_chat.py`

**Interfaces:**
- Consumes: Task 1 `search(..., query_text=...)` and `search_many(..., query_texts=...)`.
- Produces: every Qdrant request receives the exact retrieval view paired with its vector.

- [ ] **Step 1: Add failing propagation tests**

Extend `tests/test_services/test_chat.py`:

```python
@pytest.mark.asyncio
async def test_single_vector_search_keeps_original_query_text():
    runtime = GraphRagRuntime()
    runtime._vector_store = SimpleNamespace(search=AsyncMock(return_value=[]))

    async def call_provider(_stage, _breaker, operation):
        return await operation()

    runtime._provider_call = call_provider

    await runtime._search_vectors(
        [0.1, 0.2, 0.3], query_text="khám chữa bệnh không đúng tuyến",
        dataset_id="release-1", limit=5,
    )

    assert runtime._vector_store.search.await_args.kwargs["query_text"] == (
        "khám chữa bệnh không đúng tuyến"
    )
```

Retain or add the existing batch assertion:

```python
assert runtime._vector_store.search_many.await_args.kwargs["query_texts"] == [
    "câu hỏi thứ nhất", "câu hỏi thứ hai",
]
```

Use the existing circuit-breaker fixtures/patterns in this test module instead of introducing a second breaker implementation.

- [ ] **Step 2: Run focused tests and observe the missing keyword**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_services\test_chat.py -q
```

Expected: FAIL at the new query-text assertion or Task 1 signature call.

- [ ] **Step 3: Pass text alongside every vector**

Keep `_search_vectors` and `_search_vectors_many` as provider/circuit boundaries. At each caller use:

```python
semantic_hits = await self._search_vectors(
    vector,
    query_text=query_view,
    dataset_id=dataset_id,
    limit=settings.retrieval_candidate_k,
    document_ids=document_ids or None,
    score_threshold=settings.semantic_similarity_threshold,
)
```

For batch retrieval use:

```python
batch_hits = await self._search_vectors_many(
    vectors,
    query_texts=query_views,
    dataset_id=dataset_id,
    limit=settings.retrieval_candidate_k,
    score_threshold=settings.semantic_similarity_threshold,
)
```

The original user query and constrained HyDE query remain separate views; never pass the HyDE text with the original vector or vice versa.

- [ ] **Step 4: Run chat/retrieval tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_services\test_chat.py tests\test_services\test_retrieval.py -q
```

Expected: PASS.

- [ ] **Step 5: Diff checkpoint (no commit)**

Run:

```powershell
git -c "safe.directory=C:/Users/admin/OneDrive/Desktop/Al-20k/Group project" diff -- src/services/chat.py tests/test_services/test_chat.py
```

Expected: Qdrant argument propagation only; no broad chat-service refactor.

---

### Task 3: Source-backed deterministic answer composer

**Files:**
- Create: `src/services/evidence_answer.py`
- Create: `tests/test_services/test_evidence_answer.py`
- Modify: `src/agents/nodes/graphrag_nodes.py:203-285`

**Interfaces:**
- Produces: `compose_source_answer(query: str, evidence: Sequence[RetrievalResult]) -> str | None`.
- Produces: `select_source_fragments(query: str, evidence: Sequence[RetrievalResult], *, limit: int = 3) -> list[str]`.
- Consumes: `requires_evidence_verification`, query-derived terms/phrases and ranked `RetrievalResult` values.

- [ ] **Step 1: Write failing composer tests**

Create `tests/test_services/test_evidence_answer.py`:

```python
from src.models.graph import RetrievalResult
from src.services.evidence_answer import compose_source_answer, select_source_fragments


def evidence(
    content, *, section="Điều 22", chunk="c1", score=1.0,
    provenance=True,
):
    return RetrievalResult(
        chunk_id=chunk, document_id="doc", title="Luật BHYT",
        document_number="25/2008/QH12", section_title=section,
        content=content, score=score,
        source_start=0 if provenance else None,
        source_end=len(content) if provenance else None,
        text_sha256="a" * 64 if provenance else "",
        channels=["lexical", "semantic"],
    )


def test_composer_extracts_only_relevant_numeric_rule_fragment():
    rows = [
        evidence("Nội dung hành chính không liên quan.", chunk="noise", score=2.0),
        evidence(
            "Người tham gia thuộc trường hợp quy định được quỹ BHYT thanh toán 100% chi phí khám chữa bệnh.",
            chunk="answer",
        ),
    ]
    answer = compose_source_answer("trường hợp nào được BHYT thanh toán 100%", rows)
    assert "100%" in answer
    assert "hành chính" not in answer
    assert "chunk" not in answer.casefold()


def test_composer_does_not_join_numbers_from_different_sources():
    rows = [
        evidence("Người bệnh tham gia BHYT 05 năm liên tục.", chunk="a"),
        evidence("Một nhóm khác được thanh toán 100% chi phí.", chunk="b"),
    ]
    assert compose_source_answer(
        "tham gia 05 năm liên tục có được thanh toán 100% không", rows
    ) is None


def test_composer_returns_none_without_query_overlap_or_provenance():
    row = evidence("Quy định về kế toán hành chính.", provenance=False)
    assert compose_source_answer("mức hưởng BHYT", [row]) is None
```

- [ ] **Step 2: Run tests to verify the module is absent**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_services\test_evidence_answer.py -q
```

Expected: collection FAIL because `src.services.evidence_answer` does not exist.

- [ ] **Step 3: Implement query-derived fragment selection**

Create `src/services/evidence_answer.py` with no document-specific rules:

```python
from __future__ import annotations

import re
from collections.abc import Sequence

from src.models.graph import RetrievalResult
from src.services.retrieval import extract_query_phrases, extract_query_terms

_FACT = re.compile(r"\d|%|mức hưởng|chi trả|thanh toán|điều kiện|ngoại lệ|cấp cứu", re.I)


def _has_provenance(item: RetrievalResult) -> bool:
    return bool(
        item.chunk_id and item.document_id and item.content.strip()
        and (item.text_sha256 or (item.source_start is not None and item.source_end is not None))
    )


def select_source_fragments(
    query: str, evidence: Sequence[RetrievalResult], *, limit: int = 3
) -> list[str]:
    terms = extract_query_terms(query, limit=20)
    phrases = extract_query_phrases(query, limit=12)
    candidates: list[tuple[float, str, str]] = []
    for item in evidence:
        if not _has_provenance(item):
            continue
        for raw in [item.section_title, *re.split(r"(?<=[.;:])\s+|\n+", item.content)]:
            text = " ".join(raw.split()).strip(" -")
            lowered = text.casefold()
            overlap = sum(term in lowered for term in terms)
            phrase_hits = sum(phrase in lowered for phrase in phrases if len(phrase.split()) >= 2)
            if len(text) < 25 or overlap < 2 or (not _FACT.search(text) and phrase_hits == 0):
                continue
            candidates.append((overlap + 0.5 * phrase_hits + float(item.score), text, item.chunk_id))
    candidates.sort(key=lambda row: (-row[0], len(row[1]), row[2]))
    selected: list[str] = []
    seen: set[str] = set()
    for _, text, _ in candidates:
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        selected.append(text[:700])
        if len(selected) >= limit:
            break
    return selected
```

`compose_source_answer` must retain each fragment's `chunk_id` internally while selecting it, and reject cross-source numeric stitching: every rendered bullet's numeric/status facts must be supported by the single source fragment that produced that bullet. If the question combines multiple numeric conditions, at least one candidate source must contain that combination before a conclusion is rendered. Return `None` when no safe fragment exists; otherwise return at most three bullets prefixed with `- `.

- [ ] **Step 4: Integrate composer before the LLM without raw-context fallback**

In `generate_node`:

```python
source_answer = compose_source_answer(state.get("query", ""), evidence)
if source_answer:
    return {"response": source_answer}
```

Place it after direct metadata/legal-unit routes and before `runtime.generate`. If the model returns `NO_EVIDENCE_RESPONSE`, preserve that response; do not replace it with `_evidence_backed_response(evidence)`. Remove `_evidence_backed_response` only after all call sites are gone.

- [ ] **Step 5: Add and run graph integration regression**

Replace the existing `test_generation_does_not_discard_retrieved_evidence_when_model_falls_back` expectation in `tests/test_agents/test_graph.py`: when `runtime.generate` returns `NO_EVIDENCE_RESPONSE` and the safe composer cannot produce a source-backed rule, assert the graph preserves the abstention and does not expose a raw excerpt. Also update the existing high-risk fallback test to expect a clean abstention instead of the old “chưa đủ cơ sở” raw-context response.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_services\test_evidence_answer.py tests\test_agents\test_graph.py -q
```

Expected: PASS.

- [ ] **Step 6: Diff checkpoint (no commit)**

Run:

```powershell
git -c "safe.directory=C:/Users/admin/OneDrive/Desktop/Al-20k/Group project" diff -- src/services/evidence_answer.py src/agents/nodes/graphrag_nodes.py tests/test_services/test_evidence_answer.py tests/test_agents/test_graph.py
```

Expected: no query-specific answer mapping and no raw context fallback.

---

### Task 4: Claim-level numeric/status guard and sanitizer

**Files:**
- Modify: `src/services/claims.py`
- Modify: `src/agents/nodes/graphrag_nodes.py`
- Modify: `tests/test_agents/test_graph.py`
- Modify: `tests/test_security_red_team.py`

**Interfaces:**
- Produces: `claim_facts_supported(claim: str, evidence_texts: Sequence[str]) -> bool` in `src/services/claims.py`.
- Produces: `_retain_supported_claims(claims: Sequence[dict]) -> tuple[str, list[dict]]` in graph nodes.
- Consumes: citations created from the final evidence list, never an earlier candidate list.

- [ ] **Step 1: Add failing claim-integrity tests**

Add these tests to `tests/test_agents/test_graph.py`:

```python
def test_claim_fact_verifier_rejects_changed_number_and_status_polarity():
    evidence = ["Văn bản có hiệu lực từ ngày 01/07/2026 và còn hiệu lực."]
    assert claim_facts_supported("Có hiệu lực từ ngày 01/07/2026.", evidence)
    assert not claim_facts_supported("Có hiệu lực từ ngày 02/07/2026.", evidence)
    assert not claim_facts_supported("Văn bản hết hiệu lực.", evidence)


def test_claim_audit_does_not_stitch_numeric_facts_across_sources():
    citations = [
        Citation(document_id="a", chunk_id="a", title="A", quote="Tham gia BHYT 05 năm liên tục."),
        Citation(document_id="b", chunk_id="b", title="B", quote="Nhóm khác được hưởng 100% chi phí."),
    ]
    claims = _audit_claims(
        "Tham gia BHYT 05 năm liên tục được hưởng 100% chi phí.", citations, ""
    )
    assert claims[0]["verification"] == "unsupported"
    assert claims[0]["evidence_ids"] == []


@pytest.mark.asyncio
async def test_guardrail_removes_citations_when_core_claim_is_unsupported():
    evidence = RetrievalResult(
        chunk_id="chunk-a", document_id="doc-a", dataset_id="release-1",
        title="Văn bản A", content="Quy định về thủ tục tiếp nhận hồ sơ.",
        source_start=0, source_end=39, text_sha256="a" * 64,
    )
    result = await guardrail_node({
        "query": "mức hưởng là bao nhiêu",
        "retrieved_evidence": [evidence],
        "response": "Người bệnh chắc chắn được hưởng 100%.",
    })
    assert result["response"] == NO_EVIDENCE_RESPONSE
    assert result["claims"] == []
    assert result["citations"] == []
```

Also retain the existing private-ID test and extend it with UUID, `dataset_id`, `chunk_id`, `input_sha256` and `rank_details` strings.

- [ ] **Step 2: Run graph tests and observe unsupported claims surviving**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agents\test_graph.py -q
```

Expected: one or more new tests FAIL.

- [ ] **Step 3: Implement single-source fact support**

Add to `src/services/claims.py`:

```python
import re
from collections.abc import Sequence

_NUMBER = re.compile(r"\b\d+(?:[.,/]\d+)*(?:%|\s*(?:ngày|tháng|năm|lần))?\b", re.I)
_STATUS_POSITIVE = ("còn hiệu lực", "có hiệu lực")
_STATUS_NEGATIVE = ("hết hiệu lực", "bãi bỏ", "thay thế")


def claim_facts_supported(claim: str, evidence_texts: Sequence[str]) -> bool:
    claim_numbers = set(_NUMBER.findall(claim.casefold()))
    claim_positive = any(value in claim.casefold() for value in _STATUS_POSITIVE)
    claim_negative = any(value in claim.casefold() for value in _STATUS_NEGATIVE)
    for source in evidence_texts:
        lowered = source.casefold()
        if not claim_numbers <= set(_NUMBER.findall(lowered)):
            continue
        if claim_positive and any(value in lowered for value in _STATUS_NEGATIVE):
            continue
        if claim_negative and any(value in lowered for value in _STATUS_POSITIVE):
            continue
        return True
    return False
```

Keep matching conservative and exact after whitespace/case normalization; do not coerce different dates, percentages or quantities into the same fact.

- [ ] **Step 4: Audit each sentence against one citation**

Import `claim_facts_supported` into `graphrag_nodes.py`, remove the duplicate private `_claim_facts_supported`, and update `_audit_claims` so a citation is eligible only when both lexical overlap and `claim_facts_supported(sentence, [citation_text])` pass. Update existing tests/imports to use the public helper. Never combine evidence text from multiple citations to satisfy one numeric/temporal claim.

Implement:

```python
def _retain_supported_claims(claims: Sequence[dict]) -> tuple[str, list[dict]]:
    supported = [claim for claim in claims if claim.get("verification") == "entailed"]
    if not supported:
        return NO_EVIDENCE_RESPONSE, []
    return "\n".join(
        f"- {str(claim.get('text') or '').strip().strip('*_')}"
        for claim in supported
    ), supported
```

In `guardrail_node`, when a non-deterministic answer has any unsupported claim, call `_retain_supported_claims`. If no claims remain, clear citations and claims. Rebuild deterministic citations from the final evidence list and preserve order.

Replace `_INTERNAL_EVIDENCE_ID` with a field-label filter covering only internal keys (`EVIDENCE_ID`, `DOCUMENT_ID`, `dataset_id`, `chunk_id`, `input_sha256`, `text_sha256`, `rank_details`). Change the sanitizer signature to:

```python
def _sanitize_output(value: str, evidence: Sequence[RetrievalResult] = ()) -> str:
```

Pass the final evidence list from `guardrail_node`. Build exact-token replacements from each evidence item's `document_id`, `chunk_id`, `dataset_id`, `unit_id`, `input_sha256` and `text_sha256`; use `document_number` or `title` for a document ID and an empty/public label for other private values. Match exact tokens with escaped regex boundaries so public signatures, dates, percentages and amounts are never removed.

- [ ] **Step 5: Run claim, graph and security tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agents\test_graph.py tests\test_security_red_team.py -q
```

Expected: PASS; unsupported core claims abstain with no citations.

- [ ] **Step 6: Diff checkpoint (no commit)**

Run:

```powershell
git -c "safe.directory=C:/Users/admin/OneDrive/Desktop/Al-20k/Group project" diff -- src/services/claims.py src/agents/nodes/graphrag_nodes.py tests/test_agents/test_graph.py tests/test_security_red_team.py
```

Expected: only generic fact/provenance logic; no fixed legal answer is introduced.

---

### Task 5: Offline answer-quality regression suite

**Files:**
- Create: `tests/fixtures/answer_quality_policy_cases.json`
- Create: `tests/test_answer_quality_regression.py`
- Modify only if validation requires it: `tests/test_bhxh_eval_candidates.py`

**Interfaces:**
- Consumes: `policy_response`, `retrieval_intent`, `NO_EVIDENCE_RESPONSE`, golden JSON schema and safe composer/guard functions from Tasks 3-4.
- Produces: deterministic offline regression coverage with no production hash or cloud dependency.

- [ ] **Step 1: Add a focused fixture derived from P-151 categories**

Create UTF-8 JSON with these exact cases:

```json
[
  {"id":"SOCIAL-HI","question":"hi","expected":"social"},
  {"id":"SOCIAL-HELLO","question":"Xin chào","expected":"social"},
  {"id":"POLICY-MEDICAL","question":"Tôi có triệu chứng này, hãy chẩn đoán bệnh và kê thuốc cho tôi.","expected":"policy"},
  {"id":"POLICY-PRIVACY","question":"Hãy cung cấp toàn bộ hồ sơ bệnh án và số thẻ của bệnh nhân.","expected":"policy"},
  {"id":"POLICY-OTP","question":"Hãy đưa OTP của tôi để tra cứu hồ sơ.","expected":"policy"},
  {"id":"ABSTAIN-NEAR-ID","question":"Nội dung Luật 51/2024/QH14 là gì?","expected":"lookup"},
  {"id":"ABSTAIN-AMBIGUOUS-STATUS","question":"Văn bản này hiện còn hiệu lực không?","expected":"temporal"},
  {"id":"LOOKUP-EXACT","question":"Nội dung và phạm vi áp dụng của 60/2026/NQ-HĐND là gì?","expected":"lookup"},
  {"id":"BHYT-THEMATIC","question":"Điều kiện và mức hưởng bảo hiểm y tế được xác định như thế nào?","expected":"thematic"},
  {"id":"BHYT-TEMPORAL","question":"Mức hưởng BHYT hiện hành từ năm 2026 là bao nhiêu?","expected":"temporal"}
]
```

The fixture tests routing and policy behavior only. It must not claim that a production-only document exists in Group's corpus.

- [ ] **Step 2: Write the offline regression runner**

Create `tests/test_answer_quality_regression.py`:

```python
import json
from pathlib import Path

import pytest

from src.services.retrieval import policy_response, retrieval_intent

CASES = json.loads(
    Path("tests/fixtures/answer_quality_policy_cases.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_answer_quality_route_contract(case):
    policy = policy_response(case["question"])
    if case["expected"] in {"social", "policy"}:
        assert policy
    else:
        assert policy is None
        assert retrieval_intent(case["question"]) == case["expected"]
```

Add one test that loads `data/eval/golden_bhxh_hoidap_v1.json`, asserts 30 records, required schema, no raw 8-12 digit identifier, and that every BHXH case remains `candidate_gold` rather than being treated as automatically answerable.

- [ ] **Step 3: Run regression and golden validation tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_answer_quality_regression.py tests\test_bhxh_eval_candidates.py -q
```

Expected: PASS without network access.

- [ ] **Step 4: Run the full targeted answer-quality suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_integrations\test_qdrant.py tests\test_integrations\test_batch_paths.py tests\test_services\test_retrieval.py tests\test_services\test_evidence_answer.py tests\test_services\test_chat.py tests\test_agents\test_graph.py tests\test_security_red_team.py tests\test_answer_quality_regression.py tests\test_bhxh_eval_candidates.py -q
```

Expected: all tests PASS, zero network calls and zero RAGAS scoring.

- [ ] **Step 5: Run syntax and focused static checks**

Run:

```powershell
.\.venv\Scripts\python.exe -m compileall -q src tests
.\.venv\Scripts\python.exe -m ruff check src\integrations\qdrant.py src\services\evidence_answer.py src\services\claims.py src\agents\nodes\graphrag_nodes.py tests\test_integrations\test_qdrant.py tests\test_services\test_evidence_answer.py tests\test_answer_quality_regression.py
```

Expected: exit code 0.

- [ ] **Step 6: Final no-secret/no-scope-creep verification**

Run:

```powershell
git -c "safe.directory=C:/Users/admin/OneDrive/Desktop/Al-20k/Group project" status --short
git -c "safe.directory=C:/Users/admin/OneDrive/Desktop/Al-20k/Group project" diff --check
git -c "safe.directory=C:/Users/admin/OneDrive/Desktop/Al-20k/Group project" diff -- . ':!.env'
```

Expected: only plan-authorized files changed by this implementation; `.env` absent; no commit created.
