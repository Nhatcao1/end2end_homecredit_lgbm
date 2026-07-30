#!/usr/bin/env python3
"""Compare primitive HEIR and OpenFHE-Python CKKS arithmetic backends."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import shutil
from statistics import median
import sys
import time
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from code.heir.python_api import (
    OfficialCkksBinaryColumn,
    OpenFhePythonBinaryColumn,
    backend_manifest,
)


OPERATIONS = ("add", "subtract", "multiply")
LABELS = {
    "add": "CT+CT",
    "subtract": "CT-CT",
    "multiply": "CT×CT",
}
CANONICAL_BACKENDS = backend_manifest(*OPERATIONS)


def _timed(
    callable_: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> tuple[Any, float]:
    started = time.perf_counter()
    result = callable_(*args, **kwargs)
    return result, time.perf_counter() - started


def _numpy() -> Any:
    try:
        import numpy as np
    except ImportError as error:
        raise RuntimeError(
            "NumPy is required; run scripts/setup_heir_openfhe_python.sh"
        ) from error
    return np


def _inputs(
    operation: str,
    value_count: int,
    decimal_places: int,
    seed: int,
) -> tuple[Any, Any]:
    np = _numpy()
    limit = 100.0 if operation == "multiply" else 40000.0
    operation_seed = OPERATIONS.index(operation) * 1_000_003
    rng = np.random.default_rng(seed + operation_seed + value_count)
    left = np.round(
        rng.uniform(-limit, limit, value_count),
        decimal_places,
    )
    right = np.round(
        rng.uniform(-limit, limit, value_count),
        decimal_places,
    )
    return left, right


def _plain(
    operation: str,
    left: Any,
    right: Any,
) -> Any:
    np = _numpy()
    if operation == "add":
        return np.add(left, right)
    if operation == "subtract":
        return np.subtract(left, right)
    return np.multiply(left, right)


def _program(
    backend: str,
    operation: str,
    *,
    width: int,
    scale: float,
    ring_dimension: int,
    debug: bool,
) -> Any:
    if backend == "heir":
        return OfficialCkksBinaryColumn(
            operation=operation,
            width=width,
            input_scale=scale,
            debug=debug,
        )
    return OpenFhePythonBinaryColumn(
        operation=operation,
        width=width,
        input_scale=scale,
        ring_dimension=ring_dimension,
    )


def _actual_ring_dimension(program: Any) -> int | None:
    """Read the runtime ring dimension without making it a hard dependency."""
    contexts = [
        getattr(program, "_context", None),
        getattr(getattr(program, "_program", None), "crypto_context", None),
    ]
    for context in contexts:
        getter = getattr(context, "GetRingDimension", None)
        if callable(getter):
            return int(getter())
    return None


def _errors(observed: Any, expected: Any) -> dict[str, float]:
    np = _numpy()
    absolute = np.abs(observed - expected)
    relative = absolute / np.maximum(1.0, np.abs(expected))
    return {
        "mae": float(np.mean(absolute)),
        "max_abs_error": float(np.max(absolute)),
        "mean_relative_error": float(np.mean(relative)),
        "max_relative_error": float(np.max(relative)),
    }


def _passes(
    errors: dict[str, float],
    *,
    absolute_tolerance: float,
    relative_tolerance: float,
) -> bool:
    return (
        errors["max_abs_error"] <= absolute_tolerance
        or errors["max_relative_error"] <= relative_tolerance
    )


def _run_case(
    *,
    backend: str,
    operation: str,
    program: Any,
    left: Any,
    right: Any,
    expected: Any,
    width: int,
    repetitions: int,
    python_seconds: list[float],
    absolute_tolerance: float,
    relative_tolerance: float,
) -> list[dict[str, Any]]:
    np = _numpy()
    rows: list[dict[str, Any]] = []
    count = len(left)
    for repetition in range(1, repetitions + 1):
        encrypt_seconds = 0.0
        evaluation_seconds = 0.0
        decrypt_seconds = 0.0
        observed_chunks: list[Any] = []
        chunks = 0
        for start in range(0, count, width):
            stop = min(start + width, count)
            encrypted, elapsed = _timed(
                program.encrypt,
                left[start:stop],
                right[start:stop],
            )
            encrypt_seconds += elapsed
            result_ct, elapsed = _timed(program.eval, encrypted)
            evaluation_seconds += elapsed
            result, elapsed = _timed(
                program.decrypt,
                result_ct,
                valid_count=stop - start,
            )
            decrypt_seconds += elapsed
            observed_chunks.append(np.asarray(result, dtype=np.float64))
            chunks += 1
        observed = np.concatenate(observed_chunks)
        error = _errors(observed, expected)
        online_seconds = (
            encrypt_seconds + evaluation_seconds + decrypt_seconds
        )
        passed = _passes(
            error,
            absolute_tolerance=absolute_tolerance,
            relative_tolerance=relative_tolerance,
        )
        rows.append(
            {
                "backend": backend,
                "operation": operation,
                "calculation": LABELS[operation],
                "value_count": count,
                "repetition": repetition,
                "ciphertext_chunks": chunks,
                "python_seconds": python_seconds[repetition - 1],
                "encrypt_seconds": encrypt_seconds,
                "evaluation_seconds": evaluation_seconds,
                "decrypt_seconds": decrypt_seconds,
                "online_seconds": online_seconds,
                "evaluation_values_per_second": (
                    count / evaluation_seconds
                ),
                "online_values_per_second": count / online_seconds,
                **error,
                "status": "PASS" if passed else "FAIL",
            }
        )
    return rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _grouped_medians(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
    for row in rows:
        key = (
            str(row["backend"]),
            str(row["operation"]),
            int(row["value_count"]),
        )
        grouped.setdefault(key, []).append(row)
    result: list[dict[str, Any]] = []
    metrics = (
        "python_seconds",
        "encrypt_seconds",
        "evaluation_seconds",
        "decrypt_seconds",
        "online_seconds",
        "evaluation_values_per_second",
        "online_values_per_second",
        "mae",
        "max_abs_error",
        "mean_relative_error",
        "max_relative_error",
    )
    for (backend, operation, count), observations in sorted(grouped.items()):
        row: dict[str, Any] = {
            "backend": backend,
            "operation": operation,
            "calculation": LABELS[operation],
            "value_count": count,
            "status": (
                "PASS"
                if all(item["status"] == "PASS" for item in observations)
                else "FAIL"
            ),
        }
        row.update(
            {
                metric: median(
                    float(item[metric]) for item in observations
                )
                for metric in metrics
            }
        )
        result.append(row)
    return result


def _report(
    root: Path,
    medians: list[dict[str, Any]],
    setup_rows: list[dict[str, Any]],
    backends: list[str],
    absolute_tolerance: float,
    relative_tolerance: float,
) -> None:
    lines = [
        "# HEIR versus OpenFHE-Python primitive arithmetic",
        "",
        "Both backends run the same deterministic input vectors. HEIR is the "
        "canonical backend. OpenFHE Python is an explicitly requested "
        "comparison route.",
        "",
        "## One-time compilation and setup",
        "",
        "| Backend | Calculation | Runtime ring | Construct/compile (s) | "
        "Setup/keygen (s) |",
        "|---|---|---:|---:|---:|",
    ]
    for row in setup_rows:
        lines.append(
            f"| {row['backend']} | {LABELS[str(row['operation'])]} | "
            f"{row['actual_ring_dimension'] or 'unavailable'} | "
            f"{float(row['compile_seconds']):.9f} | "
            f"{float(row['setup_seconds']):.9f} |"
        )
    lines.extend(
        [
            "",
            "## Latency, throughput, and accuracy",
            "",
            "| Backend | Calculation | Values | Python (s) | Encrypt (s) | "
            "Evaluate (s) | Decrypt (s) | Online (s) | Eval values/s | "
            "Online values/s | MAE | Max abs. error | Max relative error | "
            "Status |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"
            "---:|---:|---|",
        ]
    )
    for row in medians:
        lines.append(
            f"| {row['backend']} | {row['calculation']} | "
            f"{row['value_count']} | {row['python_seconds']:.9f} | "
            f"{row['encrypt_seconds']:.9f} | "
            f"{row['evaluation_seconds']:.9f} | "
            f"{row['decrypt_seconds']:.9f} | "
            f"{row['online_seconds']:.9f} | "
            f"{row['evaluation_values_per_second']:.3f} | "
            f"{row['online_values_per_second']:.3f} | "
            f"{row['mae']:.12g} | {row['max_abs_error']:.12g} | "
            f"{row['max_relative_error']:.12g} | {row['status']} |"
        )
    if set(backends) == {"heir", "openfhe-python"}:
        indexed = {
            (str(row["backend"]), str(row["operation"]), int(row["value_count"])): row
            for row in medians
        }
        lines.extend(
            [
                "",
                "## Direct backend comparison",
                "",
                "| Calculation | Values | OpenFHE/HEIR evaluation latency | "
                "OpenFHE/HEIR online latency |",
                "|---|---:|---:|---:|",
            ]
        )
        for operation in OPERATIONS:
            counts = sorted(
                {
                    int(row["value_count"])
                    for row in medians
                    if row["operation"] == operation
                }
            )
            for count in counts:
                heir = indexed[("heir", operation, count)]
                openfhe = indexed[("openfhe-python", operation, count)]
                lines.append(
                    f"| {LABELS[operation]} | {count} | "
                    f"{openfhe['evaluation_seconds'] / heir['evaluation_seconds']:.3f}× | "
                    f"{openfhe['online_seconds'] / heir['online_seconds']:.3f}× |"
                )
    lines.extend(
        [
            "",
            "Evaluation throughput excludes encryption and decryption. Online "
            "throughput includes both. Compile/setup remain separate one-time "
            "costs.",
            "",
            "The inputs, logical width, public normalization scale, and "
            "requested ring dimension are matched. HEIR still owns its "
            "compiler-generated modulus chain; this report does not claim "
            "that HEIR and direct OpenFHE Python use an identical chain.",
            "",
            f"Acceptance: max absolute error ≤ `{absolute_tolerance:g}` or "
            f"max relative error ≤ `{relative_tolerance:g}`.",
            "",
            "Raw rows: `results.csv`. Machine-readable summary: "
            "`summary.json`.",
        ]
    )
    (root / "REPORT.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend",
        choices=("heir", "openfhe-python", "both"),
        default="both",
    )
    parser.add_argument(
        "--value-count",
        nargs="+",
        type=int,
        default=[1000],
    )
    parser.add_argument("--slot-count", type=int, default=1024)
    parser.add_argument("--ring-dimension", type=int, default=16384)
    parser.add_argument("--decimal-places", type=int, default=3)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20260731)
    parser.add_argument("--add-sub-scale", type=float, default=131072.0)
    parser.add_argument("--multiply-scale", type=float, default=256.0)
    parser.add_argument("--absolute-tolerance", type=float, default=1e-6)
    parser.add_argument("--relative-tolerance", type=float, default=1e-6)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    if args.slot_count < 2:
        parser.error("--slot-count must be at least two")
    if any(count < 1 for count in args.value_count):
        parser.error("--value-count entries must be positive")
    if args.repetitions < 1:
        parser.error("--repetitions must be positive")
    if args.decimal_places < 0:
        parser.error("--decimal-places must not be negative")

    root = args.output_dir.resolve()
    if root.exists():
        if not args.overwrite:
            raise FileExistsError(f"refusing to overwrite: {root}")
        if root == Path(root.anchor) or root == Path.home().resolve():
            raise ValueError(f"refusing to remove broad path: {root}")
        shutil.rmtree(root)
    root.mkdir(parents=True)

    backends = (
        ["heir", "openfhe-python"]
        if args.backend == "both"
        else [args.backend]
    )
    datasets: dict[tuple[str, int], tuple[Any, Any, Any, list[float]]] = {}
    for operation in OPERATIONS:
        for count in args.value_count:
            left, right = _inputs(
                operation,
                count,
                args.decimal_places,
                args.seed,
            )
            python_seconds: list[float] = []
            expected = None
            for _ in range(args.repetitions):
                expected, elapsed = _timed(
                    _plain,
                    operation,
                    left,
                    right,
                )
                python_seconds.append(elapsed)
            datasets[(operation, count)] = (
                left,
                right,
                expected,
                python_seconds,
            )

    rows: list[dict[str, Any]] = []
    setup_rows: list[dict[str, Any]] = []
    for backend in backends:
        for operation in OPERATIONS:
            scale = (
                args.multiply_scale
                if operation == "multiply"
                else args.add_sub_scale
            )
            program, compile_seconds = _timed(
                _program,
                backend,
                operation,
                width=args.slot_count,
                scale=scale,
                ring_dimension=args.ring_dimension,
                debug=args.debug,
            )
            _, setup_seconds = _timed(program.setup)
            setup_rows.append(
                {
                    "backend": backend,
                    "operation": operation,
                    "compile_seconds": compile_seconds,
                    "setup_seconds": setup_seconds,
                    "requested_ring_dimension": args.ring_dimension,
                    "actual_ring_dimension": _actual_ring_dimension(program),
                }
            )
            for count in args.value_count:
                left, right, expected, python_seconds = datasets[
                    (operation, count)
                ]
                rows.extend(
                    _run_case(
                        backend=backend,
                        operation=operation,
                        program=program,
                        left=left,
                        right=right,
                        expected=expected,
                        width=args.slot_count,
                        repetitions=args.repetitions,
                        python_seconds=python_seconds,
                        absolute_tolerance=args.absolute_tolerance,
                        relative_tolerance=args.relative_tolerance,
                    )
                )

    medians = _grouped_medians(rows)
    _write_csv(root / "results.csv", rows)
    summary = {
        "status": (
            "PASS"
            if all(row["status"] == "PASS" for row in medians)
            else "FAIL"
        ),
        "canonical_backends": CANONICAL_BACKENDS,
        "requested_backends": backends,
        "openfhe_python_is_comparison_override": (
            "openfhe-python" in backends
        ),
        "operations": list(OPERATIONS),
        "value_counts": args.value_count,
        "slot_count": args.slot_count,
        "ring_dimension": args.ring_dimension,
        "decimal_places": args.decimal_places,
        "repetitions": args.repetitions,
        "setup": setup_rows,
        "medians": medians,
    }
    (root / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n",
        encoding="utf-8",
    )
    _report(
        root,
        medians,
        setup_rows,
        backends,
        args.absolute_tolerance,
        args.relative_tolerance,
    )
    print(json.dumps(summary, indent=2))
    if summary["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
