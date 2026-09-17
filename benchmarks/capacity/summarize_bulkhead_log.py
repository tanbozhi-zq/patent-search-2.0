#!/usr/bin/env python3
"""Summarize ApplicationBulkhead events exported for one benchmark window."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmarks.capacity.benchmark_lib import summarize_bulkhead_log


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    with args.log.open(encoding="utf-8") as handle:
        summary = summarize_bulkhead_log(handle)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"SUMMARY_FILE={args.output.resolve()}")
    return 0 if "error" not in summary else 2


if __name__ == "__main__":
    raise SystemExit(main())
