#!/usr/bin/env python3
"""Build a release-scoped PageIndex-like graph from authoritative CSV/HTML.

Example:
    python scripts/build_page_index.py --source-dir data/raw
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data_pipeline.canonical import build_snapshot
from data_pipeline.page_index_export import export_page_index


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", default="data/raw")
    parser.add_argument("--output-dir", default="data/clean/page_index")
    args = parser.parse_args()
    snapshot = build_snapshot(args.source_dir)
    destination = export_page_index(snapshot, args.output_dir)
    print(f"Built PageIndex graph for {snapshot.dataset_id}: {destination}")
    print(snapshot.manifest["counts"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
