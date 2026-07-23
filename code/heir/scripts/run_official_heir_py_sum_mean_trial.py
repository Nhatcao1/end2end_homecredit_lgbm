#!/usr/bin/env python3
"""Trial SUM/MEAN using only HEIR's official Python/OpenFHE interface."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from statistics import median
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from code.heir.python_api import compile_mean, compile_sum


DEFAULT_VALUES = [160.0, -100.0, 0.0, 60.0, 250.0]


def _timed(callable_, *args, **kwargs) -> tuple[Any, float]:
    started = time.perf_counter()
    result = callable_(*args, **kwargs)
    return result, time.perf_counter() - started


def _python_reference(values: list[float], operation: str) -> tuple[float, float]:
    started = time.perf_counter()
    total = sum(values)
    result = total if operation == "sum" else total / len(values)
    return result, time.perf_counter() - started


def _run_operation(
    *,
    operation: str,
    program: Any,
    values: list[float],
    repetitions: int,
    compile_seconds: float,
    setup_seconds: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for repetition in range(1, repetitions + 1):
        expected, python_seconds = _python_reference(values, operation)
        input_ct, encrypt_seconds = _timed(program.encrypt, values)
        result_ct, evaluation_seconds = _timed(program.eval, input_ct)

        # result_ct is deliberately retained as ciphertext until this explicit
        # final audit step. No decrypt/re-encrypt occurs inside the workload.
        actual, decrypt_seconds = _timed(program.decrypt, result_ct)
        absolute_error = abs(actual - expected)
        rows.append(
            {
                "operation": operation.upper(),
                "repetition": repetition,
                "value_count": len(values),
                "width": program.width,
                "python_result": expected,
                "he_result": actual,
                "absolute_error": absolute_error,
                "python_seconds": python_seconds,
                "compile_seconds_once": compile_seconds,
                "setup_seconds_once": setup_seconds,
                "encrypt_seconds": encrypt_seconds,
                "evaluation_seconds": evaluation_seconds,
                "online_seconds": encrypt_seconds + evaluation_seconds,
                "audit_decrypt_seconds": decrypt_seconds,
                "ciphertext_retained_until_audit": True,
            }
        )
    return rows


def _summary(
    rows: list[dict[str, Any]],
    *,
    tolerance: float,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for operation in ("SUM", "MEAN"):
        selected = [row for row in rows if row["operation"] == operation]
        maximum_error = max(float(row["absolute_error"]) for row in selected)
        result.append(
            {
                "operation": operation,
                "python_result": selected[0]["python_result"],
                "he_result_last_audit": selected[-1]["he_result"],
                "max_absolute_error": maximum_error,
                "accuracy_status": "PASS" if maximum_error <= tolerance else "FAIL",
                "compile_seconds_once": selected[0]["compile_seconds_once"],
                "setup_seconds_once": selected[0]["setup_seconds_once"],
                "python_median_seconds": median(
                    float(row["python_seconds"]) for row in selected
                ),
                "encrypt_median_seconds": median(
                    float(row["encrypt_seconds"]) for row in selected
                ),
                "evaluation_median_seconds": median(
                    float(row["evaluation_seconds"]) for row in selected
                ),
                "online_median_seconds": median(
                    float(row["online_seconds"]) for row in selected
                ),
                "audit_decrypt_median_seconds": median(
                    float(row["audit_decrypt_seconds"]) for row in selected
                ),
            }
        )
    return result


def _write_report(
    path: Path,
    *,
    values: list[float],
    width: int,
    repetitions: int,
    tolerance: float,
    summary: list[dict[str, Any]],
) -> None:
    lines = [
        "# Official HEIR Python SUM/MEAN trial",
        "",
        "This trial uses `heir.compile(mlir_str=..., scheme=\"ckks\")` and the "
        "official OpenFHE Python client interface. It does not call CMake or a "
        "benchmark subprocess.",
        "",
        f"- Real values: `{values}`",
        f"- Public real count: `{len(values)}`",
        f"- Public circuit width: `{width}`",
        f"- Repetitions: `{repetitions}`",
        f"- Absolute-error tolerance: `{tolerance}`",
        "",
        "| Operation | Python | HE audit | Max abs. error | Status | "
        "Compile once (s) | Setup once (s) | Encrypt median (s) | "
        "Eval median (s) | Audit decrypt median (s) |",
        "|---|---:|---:|---:|---|---:|---:|---:|---:|---:|",
    ]
    for item in summary:
        lines.append(
            f"| {item['operation']} | {item['python_result']:.12g} | "
            f"{item['he_result_last_audit']:.12g} | "
            f"{item['max_absolute_error']:.12g} | "
            f"{item['accuracy_status']} | "
            f"{item['compile_seconds_once']:.9f} | "
            f"{item['setup_seconds_once']:.9f} | "
            f"{item['encrypt_median_seconds']:.9f} | "
            f"{item['evaluation_median_seconds']:.9f} | "
            f"{item['audit_decrypt_median_seconds']:.9f} |"
        )
    lines.extend(
        [
            "",
            "`online_seconds` in `results.csv` is encryption plus encrypted "
            "evaluation. Compilation, one-time context/key setup, and final "
            "audit decryption are reported separately.",
            "",
            "SUM and MEAN use separate compiled programs because HEIR 2026.7.1 "
            "does not expose multiple encrypted return values in its Python "
            "frontend.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--values", nargs="+", type=float, default=DEFAULT_VALUES)
    parser.add_argument("--width", type=int, default=8)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--tolerance", type=float, default=1e-6)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("benchmark_runs/official_heir_py_sum_mean_trial"),
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    values = [float(value) for value in args.values]
    if len(values) < 2:
        parser.error("provide at least two values")
    if len(values) > args.width:
        parser.error("--width must be at least the number of --values")
    if args.repetitions < 1:
        parser.error("--repetitions must be positive")

    root = args.output_dir.resolve()
    known_outputs = [
        root / "results.csv",
        root / "summary.json",
        root / "REPORT.md",
        root / "sum.mlir",
        root / "mean.mlir",
    ]
    existing = [path for path in known_outputs if path.exists()]
    if existing and not args.overwrite:
        raise FileExistsError(
            f"refusing to overwrite {existing[0]}; pass --overwrite"
        )
    root.mkdir(parents=True, exist_ok=True)

    sum_program, sum_compile_seconds = _timed(
        compile_sum,
        width=args.width,
        valid_count=len(values),
        debug=args.debug,
    )
    mean_program, mean_compile_seconds = _timed(
        compile_mean,
        width=args.width,
        valid_count=len(values),
        debug=args.debug,
    )
    _, sum_setup_seconds = _timed(sum_program.setup)
    _, mean_setup_seconds = _timed(mean_program.setup)

    rows = _run_operation(
        operation="sum",
        program=sum_program,
        values=values,
        repetitions=args.repetitions,
        compile_seconds=sum_compile_seconds,
        setup_seconds=sum_setup_seconds,
    )
    rows.extend(
        _run_operation(
            operation="mean",
            program=mean_program,
            values=values,
            repetitions=args.repetitions,
            compile_seconds=mean_compile_seconds,
            setup_seconds=mean_setup_seconds,
        )
    )
    summary = _summary(rows, tolerance=args.tolerance)

    with (root / "results.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (root / "summary.json").write_text(
        json.dumps(
            {
                "status": (
                    "PASS"
                    if all(item["accuracy_status"] == "PASS" for item in summary)
                    else "FAIL"
                ),
                "scheme": "CKKS",
                "api": "official heir.compile(mlir_str=..., scheme='ckks')",
                "values": values,
                "width": args.width,
                "repetitions": args.repetitions,
                "summary": summary,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "sum.mlir").write_text(sum_program.mlir, encoding="utf-8")
    (root / "mean.mlir").write_text(mean_program.mlir, encoding="utf-8")
    _write_report(
        root / "REPORT.md",
        values=values,
        width=args.width,
        repetitions=args.repetitions,
        tolerance=args.tolerance,
        summary=summary,
    )
    print((root / "summary.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
