#!/usr/bin/env python3
"""Prepare or execute one small source-faithful generic-column benchmark."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from code.heir.common import read_csv, write_csv, write_json, write_values
from code.heir.operations.generated_binary_backend import run_generated_binary
from code.heir.operations.source_benchmarks import SPECS, benchmark_spec, plaintext_audit_reference, prepare_binary_source_benchmark


def _errors(expected: list[float], actual: list[float], left_mask: list[float], right_mask: list[float]) -> dict[str, float | int]:
    selected = [(want, got) for want, got, left, right in zip(expected, actual, left_mask, right_mask) if left == right == 1.0]
    absolute = [abs(want - got) for want, got in selected]
    relative = [error / max(1.0, abs(want)) for (want, _), error in zip(selected, absolute)]
    return {"audited_valid_pairs": len(selected), "max_absolute_error": max(absolute, default=0.0), "max_relative_error": max(relative, default=0.0)}


def _report(path: Path, result: dict[str, Any]) -> None:
    spec = result["spec"]
    timing = result.get("timings_seconds", {})
    lines = [
        f"# {spec['benchmark_id']}",
        "",
        "## Source expression",
        "",
        f"`{spec['source_function']}` lines {spec['source_lines']}: `{spec['source_expression']}`.",
        "",
        "## Encrypted operation",
        "",
        f"`{spec['operation']}` via `{result['operation_contract']['execution_route']}` — `{spec['status']}`.",
        "",
        "Simplified generated operation (not Home-Credit-specific):",
        "",
        "```mlir",
        result.get("mlir_preview", "No generated MLIR for deferred operation."),
        "```",
        "",
        "The client packs raw nulls as `0` with separate validity vectors. It does not calculate the expression. A later encrypted `multiply` combines validity masks and masks the result before any encrypted reduction.",
        "",
        "## Timing boundary",
        "",
        "| Metric | Seconds | Included in headline |",
        "|---|---:|---|",
        f"| Original Python expression | {timing.get('python_calculation_seconds', 'not run')} | Yes |",
        f"| Encryption | {timing.get('encryption_seconds', 'not run')} | No |",
        f"| Encrypted evaluation | {timing.get('encrypted_evaluation_seconds', 'not run')} | Yes |",
        f"| Decryption audit | {timing.get('decryption_seconds', 'not run')} | No |",
        "",
        "## Accuracy audit",
        "",
        f"Only rows where both original raw inputs were valid are compared. {json.dumps(result.get('accuracy_audit', {'status': 'not run'}), sort_keys=True)}",
        "",
        "## Output boundary",
        "",
        "This standalone benchmark decrypts solely for an accuracy audit. It does not claim ciphertext pipeline continuity; that will be a separate persisted-ciphertext composition benchmark after each generic operation is validated.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_one(args: argparse.Namespace, benchmark_id: str, root: Path) -> dict[str, Any]:
    spec = benchmark_spec(benchmark_id)
    run_dir = root / benchmark_id
    run_dir.mkdir(parents=True)
    prepared = prepare_binary_source_benchmark(spec, args.data_dir, args.row_limit)
    vector_size = args.vector_size
    if prepared["row_count"] > vector_size:
        raise ValueError(f"row count {prepared['row_count']} exceeds --vector-size {vector_size}; use a smaller --row-limit")
    left, right = prepared["left"], prepared["right"]
    padded_left = left + [0.0] * (vector_size - len(left))
    padded_right = right + [0.0] * (vector_size - len(right))
    write_values(run_dir / "left_packed.csv", padded_left)
    write_values(run_dir / "right_packed.csv", padded_right)
    write_values(run_dir / "left_validity_packed.csv", prepared["left_validity"] + [0.0] * (vector_size - len(left)))
    write_values(run_dir / "right_validity_packed.csv", prepared["right_validity"] + [0.0] * (vector_size - len(right)))
    result: dict[str, Any] = {k: v for k, v in prepared.items() if k not in {"left", "right", "left_validity", "right_validity", "plaintext_reference"}}
    result["backend_status"] = "prepared_only"
    if spec.operation in {"add", "subtract", "multiply"}:
        from code.heir.operations.columns import binary_mlir
        result["mlir_preview"] = binary_mlir(vector_size, spec.operation).strip()
    if args.backend == "heir-generated-ckks":
        if spec.status != "executable_exact_ckks":
            raise NotImplementedError(f"{benchmark_id} is deliberately deferred: {spec.notes}")
        # This is the exact source expression baseline, measured independently
        # from packing/encryption and independently from the HE evaluation.
        started = time.perf_counter()
        expected = plaintext_audit_reference(prepared)
        python_seconds = time.perf_counter() - started
        backend = run_generated_binary(
            run_dir=run_dir, generated_dir=args.generated_dir, operation=spec.operation,
            vector_size=vector_size, left_path=run_dir / "left_packed.csv", right_path=run_dir / "right_packed.csv",
            openfhe_dir=args.openfhe_dir,
        )
        actual = [float(row["value"]) for row in read_csv(run_dir / "heir_decrypted.csv")][: len(expected)]
        result["backend_status"] = "heir_generated_ckks_executed"
        result["backend"] = backend
        result["timings_seconds"] = {
            "python_calculation_seconds": python_seconds,
            "encryption_seconds": backend["encryption_seconds"],
            "encrypted_evaluation_seconds": backend["encrypted_evaluation_seconds"],
            "decryption_seconds": backend["decryption_seconds"],
        }
        result["accuracy_audit"] = _errors(expected, actual, prepared["left_validity"], prepared["right_validity"])
    else:
        result["accuracy_audit"] = {"status": "not_run_prepare_only"}
    write_json(run_dir / "benchmark_manifest.json", result)
    _report(run_dir / "benchmark_report.md", result)
    return {"benchmark_id": benchmark_id, "status": result["backend_status"], "run_dir": str(run_dir)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", choices=(*(item.benchmark_id for item in SPECS), "all"), default="all")
    parser.add_argument("--data-dir", type=Path, default=Path("data/home_credit"))
    parser.add_argument("--row-limit", type=int, default=8192)
    parser.add_argument("--vector-size", type=int, default=8192)
    parser.add_argument("--backend", choices=("prepare-only", "heir-generated-ckks"), default="prepare-only")
    parser.add_argument("--generated-dir", type=Path, default=Path("benchmark_runs/generated_ckks/generic_subtract_8192"))
    parser.add_argument("--openfhe-dir", default="")
    parser.add_argument("--output-root", type=Path, default=Path("benchmark_runs/representative"))
    parser.add_argument("--run-name", default="")
    args = parser.parse_args()
    if args.row_limit < 0 or args.vector_size <= 0:
        raise ValueError("row limit must be non-negative and vector size must be positive")
    selected = [item.benchmark_id for item in SPECS] if args.benchmark == "all" else [args.benchmark]
    if args.backend == "heir-generated-ckks" and len(selected) != 1:
        raise ValueError("run one executable benchmark at a time; use --benchmark installments_payment_diff")
    name = args.run_name or f"run_{int(time.time())}"
    root = args.output_root / name
    if root.exists():
        raise FileExistsError(f"refusing to overwrite run directory: {root}")
    root.mkdir(parents=True)
    print(json.dumps([run_one(args, identifier, root) for identifier in selected], indent=2))


if __name__ == "__main__":
    main()
