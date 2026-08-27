"""Conservative, source-backed answer composition for retrieved evidence."""

from __future__ import annotations

import re
from collections.abc import Sequence

from src.models.graph import RetrievalResult
from src.services.retrieval import extract_query_phrases, extract_query_terms

_FACT = re.compile(
    r"\d|%|mức hưởng|chi trả|thanh toán|điều kiện|ngoại lệ|cấp cứu",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"\d+(?:[.,/:\-]\d+)*%?")
_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)


def _has_provenance(item: RetrievalResult) -> bool:
    if not (item.chunk_id and item.document_id and item.content.strip()):
        return False
    if _SHA256.fullmatch(item.text_sha256 or ""):
        return True
    start, end = item.source_start, item.source_end
    return (
        type(start) is int
        and type(end) is int
        and 0 <= start < end <= len(item.content)
    )


def _number_facts(value: str) -> set[str]:
    return {match.group(0).casefold() for match in _NUMBER.finditer(value)}


def _source_fragment_records(
    query: str,
    evidence: Sequence[RetrievalResult],
    *,
    limit: int,
    require_query_number_combination: bool,
) -> list[tuple[str, str]]:
    if limit <= 0:
        return []
    terms = extract_query_terms(query, limit=20)
    phrases = extract_query_phrases(query, limit=12)
    query_numbers = _number_facts(query)
    candidates: list[tuple[float, str, str]] = []

    for item in evidence:
        if not _has_provenance(item):
            continue
        for raw in re.split(r"(?<=[.;:])\s+|\n+", item.content):
            text = " ".join(raw.split()).strip(" -")
            lowered = text.casefold()
            overlap = sum(term in lowered for term in terms)
            phrase_hits = sum(
                phrase in lowered for phrase in phrases if len(phrase.split()) >= 2
            )
            if len(text) < 25 or overlap < 2 or (not _FACT.search(text) and phrase_hits == 0):
                continue
            if (
                require_query_number_combination
                and len(query_numbers) > 1
                and not query_numbers.issubset(_number_facts(text))
            ):
                continue
            candidates.append(
                (overlap + 0.5 * phrase_hits + float(item.score), text, item.chunk_id)
            )

    candidates.sort(key=lambda row: (-row[0], len(row[1]), row[2]))
    selected: list[tuple[str, str]] = []
    seen: set[str] = set()
    for _, text, chunk_id in candidates:
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        selected.append((chunk_id, text[:700]))
        if len(selected) >= limit:
            break
    return selected


def select_source_fragments(
    query: str, evidence: Sequence[RetrievalResult], *, limit: int = 3
) -> list[str]:
    """Return ranked, provenance-backed source fragments for a query."""
    return [
        text
        for _, text in _source_fragment_records(
            query,
            evidence,
            limit=limit,
            require_query_number_combination=False,
        )
    ]


def compose_source_answer(
    query: str, evidence: Sequence[RetrievalResult]
) -> str | None:
    """Render at most three complete, single-source evidence bullets.

    The composer never joins fragments or paraphrases their factual content.
    For questions containing multiple numeric conditions, it emits only a
    fragment that contains the whole numeric combination itself.
    """
    fragments = _source_fragment_records(
        query,
        evidence,
        limit=3,
        require_query_number_combination=True,
    )
    if not fragments:
        return None
    return "\n".join(f"- {text}" for _, text in fragments)
