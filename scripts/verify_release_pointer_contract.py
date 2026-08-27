#!/usr/bin/env python3
"""Read-only verification of the active/previous release pointer contract."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import psycopg


def _url(value: str) -> str:
    return value.replace("postgresql+asyncpg://", "postgresql://", 1).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.database_url:
        parser.error("DATABASE_URL or --database-url is required")

    with psycopg.connect(_url(args.database_url)) as db:
        pointer = db.execute(
            """
            SELECT singleton, active_dataset_id, previous_dataset_id, generation
            FROM ops.active_release WHERE singleton
            """
        ).fetchone()
        if pointer is None:
            raise SystemExit("ops.active_release pointer is missing")
        state = db.execute("SELECT active_dataset_id FROM dataset_state WHERE singleton").fetchone()
        active_id = str(pointer[1])
        state_id = str(state[0]) if state and state[0] else ""
        projections = db.execute(
            """
            SELECT projection_kind, status, release_fingerprint, expected_count, actual_count
            FROM release_projections WHERE dataset_id = %s ORDER BY projection_kind
            """,
            (active_id,),
        ).fetchall()
    projection_rows = [
        {
            "kind": str(row[0]),
            "status": str(row[1]),
            "fingerprint": str(row[2]),
            "expected": int(row[3]),
            "actual": int(row[4]) if row[4] is not None else None,
        }
        for row in projections
    ]
    active_pass = (
        bool(pointer[0])
        and int(pointer[3]) > 0
        and active_id == state_id
        and {row["kind"] for row in projection_rows} == {"postgres", "qdrant", "neo4j"}
        and all(row["status"] == "ready" and row["actual"] == row["expected"] for row in projection_rows)
    )
    report = {
        "active_dataset_id": active_id,
        "previous_dataset_id": str(pointer[2] or ""),
        "generation": int(pointer[3]),
        "dataset_state_matches": active_id == state_id,
        "active_projection_parity": active_pass,
        "projections": projection_rows,
        "rollback_ready": bool(pointer[2]) and active_pass,
        "pass": active_pass,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if active_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
