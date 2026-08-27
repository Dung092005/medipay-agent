"""Projection from internal audit records to the browser-safe API contract."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from src.models.schemas import ChatCitation


def public_citations(values: Iterable[Mapping[str, Any]]) -> list[ChatCitation]:
    """Return citation fields that are useful to users and safe to expose.

    Dataset/document/chunk identifiers, hashes, offsets, retrieval channels and
    claim-audit links remain server-side observability data.
    """
    citations: list[ChatCitation] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for value in values:
        title = str(value.get("title") or "").strip()
        document_number = str(value.get("document_number") or "").strip()
        section_title = str(value.get("section_title") or "").strip()
        quote = str(value.get("quote") or "").strip()
        source_url = str(value.get("source_url") or "").strip()
        if not source_url:
            source_url = _fallback_official_url(str(value.get("document_id") or ""))
        if not title and not section_title and not quote:
            continue
        identity = (title, document_number, section_title, quote, source_url)
        if identity in seen:
            continue
        seen.add(identity)
        citations.append(
            ChatCitation(
                title=title,
                document_number=document_number,
                section_title=section_title,
                quote=quote,
                source_url=source_url,
                source_checked_at=str(value.get("source_checked_at") or "").strip(),
            )
        )
    return citations


def _fallback_official_url(document_id: str) -> str:
    """Build a VBPL deep link when corpus ids are numeric VBPL item ids."""
    identifier = document_id.strip()
    if identifier.isdigit():
        return f"https://vbpl.vn/van-ban/chi-tiet/--{identifier}"
    return ""
