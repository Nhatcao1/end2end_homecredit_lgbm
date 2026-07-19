#!/usr/bin/env python3
"""Prepare one or all approved function-specific HEIR benchmark reports."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from code.heir.common import read_csv
from code.heir.function_benchmark import prepare_function_task
from code.heir.report import write_function_report
from code.heir.workloads.catalog import TASKS, get_task


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", default="all", help="B01..C02 or all")
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
    tasks = TASKS if args.task.lower() == "all" else (get_task(args.task),)
    common_run_name = args.run_name or f"prepare_{int(time.time())}"
    completed = []
    for task in tasks:
        run_dir = args.output_root / task.function_name / task.slug / common_run_name
        if run_dir.exists():
            raise FileExistsError(f"refusing to overwrite run directory: {run_dir}")
        run_dir.mkdir(parents=True)
        summary = prepare_function_task(
            task,
            args.data_dir,
            args.application,
            run_dir,
            args.application_row_limit,
            args.source_row_limit,
        )
        preview = read_csv(run_dir / "plaintext_reference.csv")
        write_function_report(run_dir / "benchmark_report.md", summary, preview)
        completed.append(
            {
                "task_id": task.task_id,
                "status": summary["backend_status"],
                "run_dir": str(run_dir),
            }
        )
    print(json.dumps(completed, indent=2))


if __name__ == "__main__":
    main()
