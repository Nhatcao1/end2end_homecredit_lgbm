#!/usr/bin/env python3
"""Simple synthetic-VND CT-CT latency and accuracy benchmark."""

from __future__ import annotations

import argparse
import csv
from importlib.metadata import PackageNotFoundError, version
import json
import math
from pathlib import Path
import shutil
from statistics import median
import sys
import time
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from code.openfhe_direct import OpenFHECreditSession


def _timed(
    function: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> tuple[Any, float]:
    started = time.perf_counter()
    result = function(*args, **kwargs)
    return result, time.perf_counter() - started


def _read_pairs(path: Path, expected_count: int) -> tuple[list[float], list[float]]:
    left: list[float] = []
    right: list[float] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"LEFT_VALUE", "RIGHT_VALUE"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"dataset is missing columns: {sorted(missing)}")
        for row in reader:
            left_value = float(row["LEFT_VALUE"])
            right_value = float(row["RIGHT_VALUE"])
            if not math.isfinite(left_value) or not math.isfinite(right_value):
                raise ValueError(f"dataset contains non-finite values: {path}")
            left.append(left_value)
            right.append(right_value)
    if len(left) != expected_count:
        raise ValueError(
            f"{path} contains {len(left)} rows; expected {expected_count}"
        )
    return left, right


def _openfhe_version() -> str:
    try:
        return version("openfhe")
    except PackageNotFoundError:
        return "test-double-or-unavailable"


def _run_repetition(
    *,
    session: OpenFHECreditSession,
    left: list[float],
    right: list[float],
    slot_count: int,
    repetition: int,
    absolute_tolerance: float,
    relative_tolerance: float,
) -> dict[str, Any]:
    python_started = time.perf_counter()
    expected = [a - b for a, b in zip(left, right)]
    python_seconds = time.perf_counter() - python_started

    observed: list[float] = []
    encrypt_seconds = evaluation_seconds = decrypt_seconds = 0.0
    for start in range(0, len(left), slot_count):
        left_block = left[start : start + slot_count]
        right_block = right[start : start + slot_count]
        # HE API call: OpenFHECreditSession.encrypt(left values)
        left_ct, elapsed = _timed(session.encrypt, left_block)
        encrypt_seconds += elapsed
        # HE API call: OpenFHECreditSession.encrypt(right values)
        right_ct, elapsed = _timed(session.encrypt, right_block)
        encrypt_seconds += elapsed
        # HE API call: OpenFHECreditSession.subtract(left_ct, right_ct)
        result_ct, elapsed = _timed(session.subtract, left_ct, right_ct)
        evaluation_seconds += elapsed
        # HE API call: OpenFHECreditSession.decrypt(final CT-CT result)
        result, elapsed = _timed(session.decrypt, result_ct)
        decrypt_seconds += elapsed
        if not isinstance(result, list):
            raise TypeError("CT-CT must decrypt to a vector")
        observed.extend(float(value) for value in result)

    absolute_errors = [
        abs(actual - reference)
        for actual, reference in zip(observed, expected)
    ]
    relative_errors = [
        error / max(1.0, abs(reference))
        for error, reference in zip(absolute_errors, expected)
    ]
    maximum_absolute = max(absolute_errors)
    maximum_relative = max(relative_errors)
    mean_absolute = sum(absolute_errors) / len(absolute_errors)
    passed = (
        maximum_absolute <= absolute_tolerance
        or maximum_relative <= relative_tolerance
    )
    online_seconds = encrypt_seconds + evaluation_seconds
    return {
        "repetition": repetition,
        "python_seconds": python_seconds,
        "encrypt_seconds": encrypt_seconds,
        "evaluate_seconds": evaluation_seconds,
        "he_online_seconds": online_seconds,
        "audit_decrypt_seconds": decrypt_seconds,
        "evaluation_values_per_second": len(left) / evaluation_seconds,
        "online_values_per_second": len(left) / online_seconds,
        "evaluation_slowdown_vs_python": evaluation_seconds / python_seconds,
        "online_slowdown_vs_python": online_seconds / python_seconds,
        "mae": mean_absolute,
        "max_abs_error": maximum_absolute,
        "max_relative_error": maximum_relative,
        "status": "PASS" if passed else "FAIL",
    }


def _write_report(
    root: Path,
    *,
    row_count: int,
    slot_count: int,
    repetitions: int,
    setup_seconds: float,
    ring_dimension: int,
    rows: list[dict[str, Any]],
) -> None:
    def metric(name: str) -> float:
        return median(float(row[name]) for row in rows)

    status = "PASS" if all(row["status"] == "PASS" for row in rows) else "FAIL"
    lines = [
        "# Synthetic VND CT-CT benchmark",
        "",
        "This isolated benchmark subtracts two encrypted synthetic VND "
        "vectors through `OpenFHECreditSession.subtract()`. It is unrelated "
        "to the credit feature pipeline.",
        "",
        f"- Rows: `{row_count}`",
        f"- Slots per ciphertext: `{slot_count}`",
        f"- Ciphertext chunks: `{(row_count + slot_count - 1) // slot_count}`",
        f"- Ring dimension: `{ring_dimension or 'OpenFHE-selected'}`",
        f"- Repetitions: `{repetitions}`",
        f"- Context/key setup: `{setup_seconds:.9f}` seconds",
        f"- Python executable: `{sys.executable}`",
        f"- OpenFHE Python: `{_openfhe_version()}`",
        "",
        "| Python | Encrypt | HE CT-CT | HE online | Audit decrypt | "
        "Online / Python | MAE | Max abs. error | Status |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        f"| {metric('python_seconds'):.9f} | "
        f"{metric('encrypt_seconds'):.9f} | "
        f"{metric('evaluate_seconds'):.9f} | "
        f"{metric('he_online_seconds'):.9f} | "
        f"{metric('audit_decrypt_seconds'):.9f} | "
        f"{metric('online_slowdown_vs_python'):.2f}x | "
        f"{max(float(row['mae']) for row in rows):.12g} | "
        f"{max(float(row['max_abs_error']) for row in rows):.12g} | "
        f"{status} |",
        "",
        "`HE online` is parent encryption plus CT-CT evaluation. Setup and "
        "final audit decryption are reported separately.",
    ]
    (root / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_subtract_count(
    *,
    dataset_path: Path,
    row_count: int,
    slot_count: int,
    repetitions: int,
    multiplicative_depth: int,
    scaling_mod_size: int,
    first_mod_size: int,
    ring_dimension: int,
    absolute_tolerance: float,
    relative_tolerance: float,
    output_dir: Path,
    overwrite: bool,
    _session_factory: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    if slot_count < 2 or slot_count & (slot_count - 1):
        raise ValueError("slot_count must be a power of two and at least two")
    if repetitions < 1:
        raise ValueError("repetitions must be positive")
    root = output_dir.resolve()
    if root.exists():
        if not overwrite:
            raise FileExistsError(f"refusing to overwrite: {root}")
        if root == Path(root.anchor) or root == Path.home().resolve():
            raise ValueError(f"refusing to remove broad path: {root}")
        shutil.rmtree(root)
    root.mkdir(parents=True)
    left, right = _read_pairs(dataset_path.resolve(), row_count)

    factory = _session_factory or OpenFHECreditSession
    # HE API call: OpenFHECreditSession(...) creates context and keys.
    session, setup_seconds = _timed(
        factory,
        slot_count=slot_count,
        multiplicative_depth=multiplicative_depth,
        scaling_mod_size=scaling_mod_size,
        first_mod_size=first_mod_size,
        ring_dimension=ring_dimension,
    )
    rows = [
        _run_repetition(
            session=session,
            left=left,
            right=right,
            slot_count=slot_count,
            repetition=repetition,
            absolute_tolerance=absolute_tolerance,
            relative_tolerance=relative_tolerance,
        )
        for repetition in range(1, repetitions + 1)
    ]
    status = "PASS" if all(row["status"] == "PASS" for row in rows) else "FAIL"
    summary = {
        "status": status,
        "backend": "official OpenFHE Python via OpenFHECreditSession",
        "operation": "CT-CT",
        "session_method": "subtract",
        "credit_feature_workload": False,
        "row_count": row_count,
        "slot_count": slot_count,
        "repetitions": repetitions,
        "setup_seconds": setup_seconds,
        "python_executable": sys.executable,
        "openfhe_python": _openfhe_version(),
        "dataset": str(dataset_path.resolve()),
    }
    with (root / "results.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (root / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_report(
        root,
        row_count=row_count,
        slot_count=slot_count,
        repetitions=repetitions,
        setup_seconds=setup_seconds,
        ring_dimension=ring_dimension,
        rows=rows,
    )
    return summary


def run_subtract_matrix(
    *,
    dataset_dir: Path,
    row_counts: list[int],
    slot_count: int,
    repetitions: int,
    multiplicative_depth: int,
    scaling_mod_size: int,
    first_mod_size: int,
    ring_dimension: int,
    absolute_tolerance: float,
    relative_tolerance: float,
    output_dir: Path,
    overwrite: bool,
    _session_factory: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    if not row_counts or any(count < 1 for count in row_counts):
        raise ValueError("row_counts must be positive")
    if len(set(row_counts)) != len(row_counts):
        raise ValueError("row_counts must be unique")
    root = output_dir.resolve()
    if root.exists():
        if not overwrite:
            raise FileExistsError(f"refusing to overwrite: {root}")
        if root == Path(root.anchor) or root == Path.home().resolve():
            raise ValueError(f"refusing to remove broad path: {root}")
        shutil.rmtree(root)
    root.mkdir(parents=True)

    runs = []
    for count in row_counts:
        child = root / f"rows_{count}"
        result = run_subtract_count(
            dataset_path=dataset_dir / f"vnd_pairs_{count}.csv",
            row_count=count,
            slot_count=slot_count,
            repetitions=repetitions,
            multiplicative_depth=multiplicative_depth,
            scaling_mod_size=scaling_mod_size,
            first_mod_size=first_mod_size,
            ring_dimension=ring_dimension,
            absolute_tolerance=absolute_tolerance,
            relative_tolerance=relative_tolerance,
            output_dir=child,
            overwrite=False,
            _session_factory=_session_factory,
        )
        runs.append({"row_count": count, "status": result["status"], "directory": child.name})
    overall = "PASS" if all(run["status"] == "PASS" for run in runs) else "FAIL"
    summary = {"status": overall, "operation": "CT-CT", "runs": runs}
    (root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (root / "REPORT.md").write_text(
        "# Synthetic VND CT-CT matrix\n\n"
        + "\n".join(
            f"- {run['row_count']} rows: [{run['status']}]({run['directory']}/REPORT.md)"
            for run in runs
        )
        + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--row-count", dest="row_counts", nargs="+", type=int, required=True)
    parser.add_argument("--slot-count", type=int, default=8192)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--multiplicative-depth", type=int, default=2)
    parser.add_argument("--scaling-mod-size", type=int, default=50)
    parser.add_argument("--first-mod-size", type=int, default=60)
    parser.add_argument("--ring-dimension", type=int, default=16384)
    parser.add_argument("--absolute-tolerance", type=float, default=1e-6)
    parser.add_argument("--relative-tolerance", type=float, default=1e-5)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    result = run_subtract_matrix(
        dataset_dir=args.dataset_dir,
        row_counts=args.row_counts,
        slot_count=args.slot_count,
        repetitions=args.repetitions,
        multiplicative_depth=args.multiplicative_depth,
        scaling_mod_size=args.scaling_mod_size,
        first_mod_size=args.first_mod_size,
        ring_dimension=args.ring_dimension,
        absolute_tolerance=args.absolute_tolerance,
        relative_tolerance=args.relative_tolerance,
        output_dir=args.output_dir,
        overwrite=args.overwrite,
    )
    print(json.dumps(result, indent=2))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
