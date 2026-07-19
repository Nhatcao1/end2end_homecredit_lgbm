#!/usr/bin/env python3
"""Run the POS_COUNT plaintext/preparation or full generated-CKKS benchmark."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from code.heir.common import path_size, read_csv, write_csv, write_json
from code.heir.report import write_pos_count_report
from code.heir.workloads.pos_count import prepare_pos_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark the Home Credit POS_COUNT feature.")
    parser.add_argument("--application", default="data/home_credit/application_train.csv")
    parser.add_argument("--pos", default="data/home_credit/POS_CASH_balance.csv")
    parser.add_argument("--application-row-limit", type=int, default=8)
    parser.add_argument("--pos-row-limit", type=int, default=0, help="0 scans the complete POS table")
    parser.add_argument("--output-root", default="benchmark_runs/pos_count")
    parser.add_argument("--run-name", default="")
    parser.add_argument("--backend", choices=("prepare-only", "heir-generated-ckks"), default="prepare-only")
    parser.add_argument("--heir-generated-dir", default="")
    parser.add_argument("--openfhe-dir", default="")
    parser.add_argument("--heir-vector-size", type=int, default=8192)
    parser.add_argument("--accuracy-tolerance", type=float, default=1e-4)
    return parser.parse_args()


def compare(reference: list[dict[str, str]], actual: list[dict[str, str]], tolerance: float) -> dict[str, Any]:
    actual_by_index = {row["app_index"]: float(row["POS_COUNT"]) for row in actual}
    details: list[dict[str, Any]] = []
    for row in reference:
        expected = float(row["POS_COUNT"])
        observed = actual_by_index.get(row["app_index"])
        error = abs(expected - observed) if observed is not None else None
        details.append(
            {
                "app_index": row["app_index"],
                "POS_COUNT": expected,
                "actual_POS_COUNT": observed if observed is not None else "",
                "absolute_error": error if error is not None else "",
                "passed": error is not None and error <= tolerance,
            }
        )
    errors = [row["absolute_error"] for row in details if row["absolute_error"] != ""]
    return {
        "passed": len(actual_by_index) == len(reference) and all(row["passed"] for row in details),
        "tolerance": tolerance,
        "checked_rows": len(details),
        "max_absolute_error": max(errors) if errors else None,
        "mean_absolute_error": sum(errors) / len(errors) if errors else None,
        "details": details,
    }


def main() -> None:
    args = parse_args()
    run_name = args.run_name or f"pos_count_{int(time.time())}"
    run_dir = Path(args.output_root) / run_name
    if run_dir.exists() and any(run_dir.iterdir()):
        raise FileExistsError(f"refusing to overwrite non-empty run directory: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)

    summary = prepare_pos_count(
        Path(args.application),
        Path(args.pos),
        run_dir,
        args.application_row_limit,
        args.pos_row_limit,
    )
    summary.update(
        {
            "backend_requested": args.backend,
            "heir_scheme": "CKKS",
            "heir_vector_size": args.heir_vector_size,
            "accuracy_tolerance": args.accuracy_tolerance,
        }
    )
    reference = read_csv(run_dir / "plaintext_reference.csv")
    preview: list[dict[str, Any]] = [dict(row) for row in reference]

    if args.backend == "heir-generated-ckks":
        try:
            from code.heir.backends.generated_ckks import run_generated_pos_count

            if not args.heir_generated_dir:
                raise ValueError("--heir-generated-dir is required for the generated CKKS backend")
            result, backend_timings, log = run_generated_pos_count(
                run_dir,
                Path(args.heir_generated_dir),
                args.openfhe_dir,
                args.heir_vector_size,
                int(summary["application_rows"]),
                int(summary["slots_per_application"]),
            )
            (run_dir / "heir_generated_ckks.log").write_text(log, encoding="utf-8")
            summary["timings_seconds"].update(backend_timings)
            summary["heir_result"] = result
            correctness = compare(reference, read_csv(run_dir / "heir_decrypted.csv"), args.accuracy_tolerance)
            summary["correctness"] = correctness
            write_csv(
                run_dir / "accuracy.csv",
                ["app_index", "POS_COUNT", "actual_POS_COUNT", "absolute_error", "passed"],
                correctness["details"],
            )
            preview = correctness["details"]
            summary["backend_status"] = "heir_generated_ckks_completed"
        except Exception as error:
            summary["backend_status"] = "heir_generated_ckks_failed"
            summary["backend_error"] = f"{type(error).__name__}: {error}"

    summary["artifact_sizes_bytes"] = {
        "run_directory": path_size(run_dir),
        "tensors": path_size(run_dir / "tensors"),
        "client_private_mapping": path_size(run_dir / "client_private" / "applicant_mapping.csv"),
        "plaintext_reference": path_size(run_dir / "plaintext_reference.csv"),
        "generated_ckks_work": path_size(run_dir / "heir_generated_ckks"),
    }
    write_json(run_dir / "benchmark_summary.json", summary)
    write_pos_count_report(run_dir / "benchmark_report.md", summary, preview)
    print(json.dumps(summary, indent=2))

    if summary.get("backend_status") == "heir_generated_ckks_failed":
        raise SystemExit(summary["backend_error"])
    if summary.get("correctness", {}).get("passed") is False:
        raise SystemExit("CKKS accuracy acceptance failed")


if __name__ == "__main__":
    main()
