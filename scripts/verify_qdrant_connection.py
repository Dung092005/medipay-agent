#!/usr/bin/env python3
"""Smoke-check Qdrant credentials from .env (no upserts)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    url = (os.getenv("QDRANT_URL") or "").strip()
    api_key = (os.getenv("QDRANT_API_KEY") or "").strip()
    alias = (os.getenv("QDRANT_COLLECTION") or "medical_legal_active").strip()
    report: dict = {
        "ok": False,
        "has_url": bool(url),
        "has_api_key": bool(api_key),
        "alias": alias,
    }
    if not url or not api_key:
        report["error"] = "Set QDRANT_URL and QDRANT_API_KEY in .env"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1
    try:
        from qdrant_client import QdrantClient
    except ImportError:
        report["error"] = 'Install with: pip install "qdrant-client>=1.9.0"'
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1
    client = QdrantClient(url=url, api_key=api_key, timeout=30)
    collections = [item.name for item in client.get_collections().collections]
    aliases = {item.alias_name: item.collection_name for item in client.get_aliases().aliases}
    report["collections"] = collections
    report["aliases"] = aliases
    target = aliases.get(alias)
    report["alias_points_to"] = target
    if target and client.collection_exists(target):
        info = client.get_collection(target)
        report["alias_points_count"] = int(info.points_count or 0)
        report["alias_vector_size"] = getattr(
            getattr(info.config.params, "vectors", None), "size", None
        )
    report["ok"] = True
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
