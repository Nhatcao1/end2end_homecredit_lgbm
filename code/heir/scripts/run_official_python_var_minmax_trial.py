#!/usr/bin/env python3
"""Trial encrypted VAR with HEIR Python and MIN/MAX with OpenFHE Python."""

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

from code.heir.python_api import (
    EncryptedMinMax,
    OfficialOpenFheMinMax,
    compile_variance,
    public_power_of_two_scale,
)


DEFAULT_VALUES = [160.0, -100.0, 0.0, 60.0, 250.0]


def _timed(callable_, *args, **kwargs) -> tuple[Any, float]:
    started = time.perf_counter()
    result = callable_(*args, **kwargs)
    return result, time.perf_counter() - started


def _sample_variance(values: list[float]) -> float:
    mean = sum(values) / len(values)
    return sum((value - mean) ** 2 for value in values) / (len(values) - 1)


def _status(
    *,
    actual: float,
    expected: float,
    relative_tolerance: float,
    absolute_tolerance: float,
) -> tuple[float, float, str]:
    absolute_error = abs(actual - expected)
    relative_error = absolute_error / max(1.0, abs(expected))
    passed = (
        absolute_error <= absolute_tolerance
        or relative_error <= relative_tolerance
    )
    return absolute_error, relative_error, "PASS" if passed else "FAIL"


def _report(
    *,
    values: list[float],
    width: int,
    scale: float,
    rows: list[dict[str, Any]],
    setup: dict[str, float],
) -> str:
    lines = [
        "# Official Python VAR/MIN/MAX trial",
        "",
        "`VAR` is compiled by official HEIR Python to CKKS. `MIN` and `MAX` "
        "use the official OpenFHE Python CKKS↔FHEW API. These are separate "
        "cryptographic contexts and their ciphertext objects are not "
        "interchangeable.",
        "",
        f"- Values: `{values}`",
        f"- HEIR VAR width: `{width}`",
        f"- MIN/MAX public representation scale: `{scale}`",
        "",
        "| Output | Python | HE audit | Abs. error | Relative error | Status | "
        "Encrypt median (s) | Eval median (s) | Audit decrypt median (s) |",
        "|---|---:|---:|---:|---:|---|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['operation']} | {row['python_result']:.12g} | "
            f"{row['he_result']:.12g} | {row['absolute_error']:.12g} | "
            f"{row['relative_error']:.12g} | {row['status']} | "
            f"{row['encrypt_median_seconds']:.9f} | "
            f"{row['evaluation_median_seconds']:.9f} | "
            f"{row['audit_decrypt_median_seconds']:.9f} |"
        )
    lines.extend(
        [
            "",
            "| One-time stage | Seconds |",
            "|---|---:|",
            f"| HEIR VAR compilation | {setup['variance_compile_seconds']:.9f} |",
            f"| HEIR VAR context/key setup | {setup['variance_setup_seconds']:.9f} |",
            f"| OpenFHE MIN/MAX context/switching-key setup | {setup['minmax_setup_seconds']:.9f} |",
            "",
            "MIN and MAX reuse one encrypted candidate vector inside their "
            "OpenFHE context. VAR uses its own HEIR-generated context because "
            "the current official APIs do not provide cross-runtime ciphertext "
            "interchange.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--values", nargs="+", type=float, default=DEFAULT_VALUES)
    parser.add_argument("--width", type=int, default=8)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--ring-dimension", type=int, default=16384)
    parser.add_argument("--input-scale", type=float, default=0.0)
    parser.add_argument("--relative-tolerance", type=float, default=1e-5)
    parser.add_argument("--minmax-absolute-tolerance", type=float, default=1.0)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("benchmark_runs/official_python_var_minmax_trial"),
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
    scale = (
        args.input_scale
        if args.input_scale > 0
        else public_power_of_two_scale(values)
    )

    root = args.output_dir.resolve()
    known = [
        root / "results.csv",
        root / "summary.json",
        root / "REPORT.md",
        root / "variance.mlir",
    ]
    existing = [path for path in known if path.exists()]
    if existing and not args.overwrite:
        raise FileExistsError(
            f"refusing to overwrite {existing[0]}; pass --overwrite"
        )
    root.mkdir(parents=True, exist_ok=True)

    variance_program, variance_compile_seconds = _timed(
        compile_variance,
        width=args.width,
        valid_count=len(values),
        debug=args.debug,
    )
    _, variance_setup_seconds = _timed(variance_program.setup)
    minmax_program = OfficialOpenFheMinMax(
        valid_count=len(values),
        input_scale=scale,
        ring_dimension=args.ring_dimension,
    )
    _, minmax_setup_seconds = _timed(minmax_program.setup)

    expected = {
        "VAR": _sample_variance(values),
        "MIN": min(values),
        "MAX": max(values),
    }
    raw: dict[str, list[dict[str, float]]] = {
        "VAR": [],
        "MIN": [],
        "MAX": [],
    }
    for _ in range(args.repetitions):
        variance_input, var_encrypt = _timed(variance_program.encrypt, values)
        variance_ct, var_eval = _timed(variance_program.eval, variance_input)
        variance_value, var_decrypt = _timed(
            variance_program.decrypt,
            variance_ct,
        )
        raw["VAR"].append(
            {
                "result": variance_value,
                "encrypt": var_encrypt,
                "eval": var_eval,
                "decrypt": var_decrypt,
            }
        )

        minmax_input, minmax_encrypt = _timed(minmax_program.encrypt, values)
        minimum_ct, minimum_eval = _timed(
            minmax_program.eval_min,
            minmax_input,
        )
        maximum_ct, maximum_eval = _timed(
            minmax_program.eval_max,
            minmax_input,
        )
        (minimum_value, maximum_value), minmax_decrypt = _timed(
            minmax_program.decrypt,
            EncryptedMinMax(minimum_ct, maximum_ct),
        )
        # One audit call decrypts two outputs; record the same boundary time on
        # both rows instead of pretending they were separate decryptions.
        raw["MIN"].append(
            {
                "result": minimum_value,
                "encrypt": minmax_encrypt,
                "eval": minimum_eval,
                "decrypt": minmax_decrypt,
            }
        )
        raw["MAX"].append(
            {
                "result": maximum_value,
                "encrypt": minmax_encrypt,
                "eval": maximum_eval,
                "decrypt": minmax_decrypt,
            }
        )

    rows: list[dict[str, Any]] = []
    for operation in ("VAR", "MIN", "MAX"):
        observations = raw[operation]
        he_result = observations[-1]["result"]
        absolute_tolerance = (
            args.minmax_absolute_tolerance if operation in {"MIN", "MAX"} else 0.0
        )
        absolute_error, relative_error, status = _status(
            actual=he_result,
            expected=expected[operation],
            relative_tolerance=args.relative_tolerance,
            absolute_tolerance=absolute_tolerance,
        )
        rows.append(
            {
                "operation": operation,
                "python_result": expected[operation],
                "he_result": he_result,
                "absolute_error": absolute_error,
                "relative_error": relative_error,
                "status": status,
                "encrypt_median_seconds": median(
                    item["encrypt"] for item in observations
                ),
                "evaluation_median_seconds": median(
                    item["eval"] for item in observations
                ),
                "audit_decrypt_median_seconds": median(
                    item["decrypt"] for item in observations
                ),
            }
        )

    setup = {
        "variance_compile_seconds": variance_compile_seconds,
        "variance_setup_seconds": variance_setup_seconds,
        "minmax_setup_seconds": minmax_setup_seconds,
    }
    with (root / "results.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = {
        "status": (
            "PASS" if all(row["status"] == "PASS" for row in rows) else "FAIL"
        ),
        "values": values,
        "variance_api": "official HEIR Python CKKS",
        "minmax_api": "official OpenFHE Python CKKS-to-FHEW",
        "separate_contexts": True,
        "input_scale": scale,
        "ring_dimension": args.ring_dimension,
        "repetitions": args.repetitions,
        "setup": setup,
        "results": rows,
    }
    (root / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n",
        encoding="utf-8",
    )
    (root / "variance.mlir").write_text(
        variance_program.mlir,
        encoding="utf-8",
    )
    (root / "REPORT.md").write_text(
        _report(
            values=values,
            width=args.width,
            scale=scale,
            rows=rows,
            setup=setup,
        ),
        encoding="utf-8",
    )
    print((root / "summary.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
