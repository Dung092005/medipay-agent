#!/usr/bin/env python3
"""Build release-scoped deterministic facet memberships."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data_pipeline.canonical import build_snapshot
from data_pipeline.facets import build_facets, write_facets_csv


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", default="data/raw")
    parser.add_argument("--output-dir", default="data/clean/facets")
    args = parser.parse_args()
    snapshot = build_snapshot(args.source_dir)
    path = write_facets_csv(snapshot, args.output_dir)
    print(f"Wrote {len(build_facets(snapshot))} facet memberships to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
