#!/usr/bin/env python3
"""Prepare one complete source-function HEIR benchmark report."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from code.heir.common import read_csv
from code.heir.function_benchmark import prepare_complete_function
from code.heir.report import write_complete_function_report
from code.heir.workloads.catalog import FUNCTIONS, get_function


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--function",
        required=True,
        help="bureau, previous, pos, installments, credit_card, or all",
    )
    parser.add_argument("--data-dir", type=Path, default=Path("data/home_credit"))
    parser.add_argument(
        "--application", type=Path, default=Path("data/home_credit/application_train.csv")
    )
    parser.add_argument("--application-row-limit", type=int, default=8)
    parser.add_argument("--source-row-limit", type=int, default=0)
    parser.add_argument("--output-root", type=Path, default=Path("benchmark_runs/functions"))
    parser.add_argument("--run-name", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    functions = FUNCTIONS if args.function.lower() == "all" else (get_function(args.function),)
    common_run_name = args.run_name or f"prepare_{int(time.time())}"
    completed = []
    for function in functions:
        run_dir = args.output_root / function.function_name / common_run_name
        if run_dir.exists():
            raise FileExistsError(f"refusing to overwrite run directory: {run_dir}")
        run_dir.mkdir(parents=True)
        summary = prepare_complete_function(
            function,
            args.data_dir,
            args.application,
            run_dir,
            args.application_row_limit,
            args.source_row_limit,
        )
        preview = read_csv(run_dir / "plaintext_reference.csv")
        write_complete_function_report(run_dir / "benchmark_report.md", summary, preview)
        completed.append(
            {
                "benchmark_id": function.benchmark_id,
                "function": function.name,
                "status": summary["backend_status"],
                "bundle_status": summary["bundle_status"],
                "run_dir": str(run_dir),
            }
        )
    print(json.dumps(completed, indent=2))


if __name__ == "__main__":
    main()
