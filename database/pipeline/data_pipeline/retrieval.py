"""Query planning and observable multi-channel fusion primitives.

Database adapters can feed these pure contracts with exact, lexical, vector,
legal-edge and community results.  The fusion function never turns a retrieval
score into legal authority; it only ranks evidence candidates.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class RetrievalChannel(StrEnum):
    EXACT = "exact"
    LEXICAL = "lexical"
    SEMANTIC = "semantic"
    LEGAL_GRAPH = "legal_graph"


class QueryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2_000)
    intent: str = "thematic"
    document_numbers: list[str] = Field(default_factory=list)
    legal_labels: list[str] = Field(default_factory=list)
    category: str | None = None
    reference_date: str | None = None
    jurisdiction: str | None = None
    subqueries: list[str] = Field(default_factory=list, max_length=4)
    channels: list[RetrievalChannel] = Field(
        default_factory=lambda: [
            RetrievalChannel.EXACT,
            RetrievalChannel.LEXICAL,
            RetrievalChannel.SEMANTIC,
            RetrievalChannel.LEGAL_GRAPH,
        ]
    )
    planner_version: str = "query-planner-v1"


class EvidenceHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    passage_id: str | None = None
    unit_id: str | None = None
    text: str = ""
    channel: RetrievalChannel
    score: float
    rank: int = Field(ge=1)
    citation: dict[str, str] = Field(default_factory=dict)


def build_query_plan(
    query: str,
    *,
    category: str | None = None,
    reference_date: str | None = None,
    jurisdiction: str | None = None,
) -> QueryPlan:
    """Extract only high-precision routing hints; semantic decomposition stays optional."""

    normalized = query.strip()
    if not normalized:
        raise ValueError("query must not be blank")
    numbers = sorted(set(re.findall(r"\b\d+/\d{4}/[A-ZĐ-]+", normalized.upper())))
    labels = sorted(set(re.findall(r"\b(?:Điều|Khoản)\s+\d+[A-Za-z]?|\b[a-zđ]\)", normalized, flags=re.IGNORECASE)))
    lowered = normalized.casefold()
    if any(token in lowered for token in ("chi trả", "mức hưởng", "điều kiện", "đối tượng")):
        intent = "eligibility"
    elif numbers or labels:
        intent = "lookup"
    elif any(token in lowered for token in ("hiệu lực", "từ ngày", "năm ")):
        intent = "temporal"
    else:
        intent = "thematic"
    return QueryPlan(
        query=normalized,
        intent=intent,
        document_numbers=numbers,
        legal_labels=labels,
        category=category,
        reference_date=reference_date,
        jurisdiction=jurisdiction,
        subqueries=[normalized],
    )


def reciprocal_rank_fusion(
    channel_hits: dict[RetrievalChannel, Sequence[EvidenceHit]],
    *,
    k: int = 60,
) -> list[EvidenceHit]:
    """Fuse channels while retaining per-channel score and citation provenance."""

    if k <= 0:
        raise ValueError("k must be positive")
    fused: dict[str, EvidenceHit] = {}
    rrf_score: defaultdict[str, float] = defaultdict(float)
    channels: defaultdict[str, list[str]] = defaultdict(list)
    for channel, hits in channel_hits.items():
        for rank, hit in enumerate(hits, start=1):
            rrf_score[hit.evidence_id] += 1.0 / (k + rank)
            channels[hit.evidence_id].append(channel.value)
            previous = fused.get(hit.evidence_id)
            if previous is None or hit.score > previous.score:
                fused[hit.evidence_id] = hit.model_copy(update={"channel": channel})
    result: list[EvidenceHit] = []
    for rank, evidence_id in enumerate(sorted(fused, key=lambda item: (-rrf_score[item], item)), start=1):
        hit = fused[evidence_id]
        citation = dict(hit.citation)
        citation["channels"] = ",".join(sorted(set(channels[evidence_id])))
        citation["rrf_score"] = f"{rrf_score[evidence_id]:.8f}"
        result.append(hit.model_copy(update={"rank": rank, "score": rrf_score[evidence_id], "citation": citation}))
    return result
