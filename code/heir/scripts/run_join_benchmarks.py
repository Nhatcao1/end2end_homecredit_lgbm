#!/usr/bin/env python3
"""Benchmark individual and end-to-end joins of aligned function bundles."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from code.heir.join_benchmark import run_join_benchmarks
from code.heir.workloads.catalog import FUNCTIONS


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--function-run-root",
        type=Path,
        default=Path("benchmark_runs/functions"),
    )
    parser.add_argument(
        "--function-run-name",
        required=True,
        help="common run name previously passed to run_function_benchmarks.py",
    )
    parser.add_argument(
        "--output-root", type=Path, default=Path("benchmark_runs/joins")
    )
    parser.add_argument("--run-name", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_name = args.run_name or f"join_{int(time.time())}"
    function_runs = [
        (
            function,
            args.function_run_root
            / function.function_name
            / args.function_run_name,
        )
        for function in FUNCTIONS
    ]
    result = run_join_benchmarks(function_runs, args.output_root / run_name)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
