#!/usr/bin/env python3
"""Extract release-scoped CSV artifacts for every HTML table in the corpus.

Example:
    python scripts/extract_tables.py --source-dir data/raw
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data_pipeline.canonical import build_snapshot
from data_pipeline.tables import write_snapshot_table_csv


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", default="data/raw")
    parser.add_argument("--output-dir", default="data/clean/tables")
    args = parser.parse_args()
    snapshot = build_snapshot(args.source_dir)
    artifact = write_snapshot_table_csv(snapshot, args.output_dir)
    print(f"Extracted {artifact.table_count} tables and {artifact.cell_count} cells for {artifact.dataset_id}")
    print(artifact.tables_path)
    print(artifact.cells_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
