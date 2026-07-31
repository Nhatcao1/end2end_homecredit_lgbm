#!/usr/bin/env python3
"""Simple synthetic-VND BGV CT+CT latency and exactness benchmark."""

from __future__ import annotations

import argparse
import csv
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import shutil
from statistics import median
import sys
import time
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from code.openfhe_direct import OpenFHEBgvSession


def _timed(
    function: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> tuple[Any, float]:
    started = time.perf_counter()
    result = function(*args, **kwargs)
    return result, time.perf_counter() - started


def _read_pairs(path: Path, expected_count: int) -> tuple[list[int], list[int]]:
    left: list[int] = []
    right: list[int] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"LEFT_VALUE", "RIGHT_VALUE"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"dataset is missing columns: {sorted(missing)}")
        for row in reader:
            left_value = int(row["LEFT_VALUE"])
            right_value = int(row["RIGHT_VALUE"])
            left.append(left_value)
            right.append(right_value)
    if len(left) != expected_count:
        raise ValueError(
            f"{path} contains {len(left)} values; expected {expected_count}"
        )
    return left, right


def _openfhe_version() -> str:
    try:
        return version("openfhe")
    except PackageNotFoundError:
        return "test-double-or-unavailable"


def _numpy_reference_add(
    left: list[int],
    right: list[int],
) -> tuple[list[int], float]:
    """Time only optimized NumPy addition, excluding array preparation."""
    try:
        import numpy as np
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "the CT+CT plaintext baseline requires NumPy; install it in the "
            "active environment with: python3 -m pip install numpy"
        ) from error
    left_array = np.asarray(left, dtype=np.int64)
    right_array = np.asarray(right, dtype=np.int64)
    started = time.perf_counter()
    expected_array = np.add(left_array, right_array)
    return expected_array.tolist(), time.perf_counter() - started


def _run_repetition(
    *,
    session: OpenFHEBgvSession,
    left: list[int],
    right: list[int],
    slot_count: int,
    repetition: int,
) -> dict[str, Any]:
    # NumPy is the optimized plaintext baseline. Pandas would ultimately use
    # this same vectorized addition but would also time Series/index overhead.
    expected, python_seconds = _numpy_reference_add(left, right)

    observed: list[int] = []
    encrypt_seconds = evaluation_seconds = decrypt_seconds = 0.0
    for start in range(0, len(left), slot_count):
        left_block = left[start : start + slot_count]
        right_block = right[start : start + slot_count]
        # HE API call: OpenFHEBgvSession.encrypt(left values)
        left_ct, elapsed = _timed(session.encrypt, left_block)
        encrypt_seconds += elapsed
        # HE API call: OpenFHEBgvSession.encrypt(right values)
        right_ct, elapsed = _timed(session.encrypt, right_block)
        encrypt_seconds += elapsed
        # HE API call: OpenFHEBgvSession.add(left_ct, right_ct)
        result_ct, elapsed = _timed(session.add, left_ct, right_ct)
        evaluation_seconds += elapsed
        # HE API call: OpenFHEBgvSession.decrypt(final CT+CT result)
        result, elapsed = _timed(session.decrypt, result_ct)
        decrypt_seconds += elapsed
        if not isinstance(result, list):
            raise TypeError("CT+CT must decrypt to a vector")
        observed.extend(int(value) for value in result)

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
    passed = maximum_absolute == 0
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
    value_count: int,
    slot_count: int,
    repetitions: int,
    setup_seconds: float,
    ring_dimension: int,
    plaintext_modulus: int,
    rows: list[dict[str, Any]],
    left: list[int],
    right: list[int],
    expected: list[int],
) -> None:
    def metric(name: str) -> float:
        return median(float(row[name]) for row in rows)

    status = "PASS" if all(row["status"] == "PASS" for row in rows) else "FAIL"
    lines = [
        "# Synthetic VND BGV CT+CT benchmark",
        "",
        "This isolated benchmark adds two encrypted integer VND vectors "
        "through `OpenFHEBgvSession.add()`. It is unrelated "
        "to the credit feature pipeline.",
        "",
        f"- Vector length: `{value_count}` values in A and B",
        f"- Slots per ciphertext: `{slot_count}`",
        f"- Ciphertext chunks per vector: "
        f"`{(value_count + slot_count - 1) // slot_count}`",
        f"- Ring dimension: `{ring_dimension or 'OpenFHE-selected'}`",
        f"- BGV plaintext modulus: `{plaintext_modulus}`",
        f"- Maximum positive centered value: `{plaintext_modulus // 2}`",
        f"- Repetitions: `{repetitions}`",
        f"- Observed operand range: `{min(min(left), min(right)):.0f}` to "
        f"`{max(max(left), max(right)):.0f}` VND",
        f"- Expected-result range: `{min(expected):.0f}` to "
        f"`{max(expected):.0f}` VND",
        "- Plaintext reference: `numpy.add(int64)`",
        "- Acceptance: every decrypted value must exactly equal A+B",
        f"- Context/key setup: `{setup_seconds:.9f}` seconds",
        f"- Python executable: `{sys.executable}`",
        f"- OpenFHE Python: `{_openfhe_version()}`",
        "",
        "| NumPy add | Encrypt | HE CT+CT | HE online | Audit decrypt | "
        "Online / NumPy | MAE | Max abs. error | Max relative error | Status |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        f"| {metric('python_seconds'):.9f} | "
        f"{metric('encrypt_seconds'):.9f} | "
        f"{metric('evaluate_seconds'):.9f} | "
        f"{metric('he_online_seconds'):.9f} | "
        f"{metric('audit_decrypt_seconds'):.9f} | "
        f"{metric('online_slowdown_vs_python'):.2f}x | "
        f"{max(float(row['mae']) for row in rows):.12g} | "
        f"{max(float(row['max_abs_error']) for row in rows):.12g} | "
        f"{max(float(row['max_relative_error']) for row in rows):.12g} | "
        f"{status} |",
        "",
        "`HE online` is parent encryption plus CT+CT evaluation. Setup and "
        "final audit decryption are reported separately.",
        "",
        "The configured minimum and maximum are generation boundaries, not "
        "repeated operands. Values are deterministic random integers inside "
        "that range. BGV is exact while the result remains inside the "
        "centered plaintext-modulus range.",
    ]
    (root / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_add_count(
    *,
    dataset_path: Path,
    value_count: int,
    slot_count: int,
    repetitions: int,
    multiplicative_depth: int,
    plaintext_modulus: int,
    ring_dimension: int,
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
    left, right = _read_pairs(dataset_path.resolve(), value_count)
    expected = [a + b for a, b in zip(left, right)]
    if max(expected) >= plaintext_modulus // 2:
        raise ValueError(
            "plaintext_modulus is unsafe: maximum A+B must be below half "
            "the modulus for positive centered BGV decoding"
        )

    factory = _session_factory or OpenFHEBgvSession
    # HE API call: OpenFHEBgvSession(...) creates context and keys.
    session, setup_seconds = _timed(
        factory,
        slot_count=slot_count,
        plaintext_modulus=plaintext_modulus,
        multiplicative_depth=multiplicative_depth,
        ring_dimension=ring_dimension,
    )
    rows = [
        _run_repetition(
            session=session,
            left=left,
            right=right,
            slot_count=slot_count,
            repetition=repetition,
        )
        for repetition in range(1, repetitions + 1)
    ]
    status = "PASS" if all(row["status"] == "PASS" for row in rows) else "FAIL"
    summary = {
        "status": status,
        "scheme": "BGV",
        "backend": "official OpenFHE Python via OpenFHEBgvSession",
        "operation": "CT+CT",
        "session_method": "add",
        "credit_feature_workload": False,
        "value_count": value_count,
        "slot_count": slot_count,
        "plaintext_modulus": plaintext_modulus,
        "centered_capacity": plaintext_modulus // 2,
        "repetitions": repetitions,
        "setup_seconds": setup_seconds,
        "python_executable": sys.executable,
        "openfhe_python": _openfhe_version(),
        "dataset": str(dataset_path.resolve()),
        "observed_operand_range": [
            min(min(left), min(right)),
            max(max(left), max(right)),
        ],
        "expected_result_range": [min(expected), max(expected)],
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
        value_count=value_count,
        slot_count=slot_count,
        repetitions=repetitions,
        setup_seconds=setup_seconds,
        ring_dimension=ring_dimension,
        plaintext_modulus=plaintext_modulus,
        rows=rows,
        left=left,
        right=right,
        expected=expected,
    )
    return summary


def run_add_matrix(
    *,
    dataset_dir: Path,
    value_counts: list[int],
    slot_count: int,
    repetitions: int,
    multiplicative_depth: int,
    plaintext_modulus: int,
    ring_dimension: int,
    output_dir: Path,
    overwrite: bool,
    _session_factory: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    if not value_counts or any(count < 1 for count in value_counts):
        raise ValueError("value_counts must be positive")
    if len(set(value_counts)) != len(value_counts):
        raise ValueError("value_counts must be unique")
    root = output_dir.resolve()
    if root.exists():
        if not overwrite:
            raise FileExistsError(f"refusing to overwrite: {root}")
        if root == Path(root.anchor) or root == Path.home().resolve():
            raise ValueError(f"refusing to remove broad path: {root}")
        shutil.rmtree(root)
    root.mkdir(parents=True)

    runs = []
    for count in value_counts:
        child = root / f"values_{count}"
        result = run_add_count(
            dataset_path=dataset_dir / f"vnd_pairs_{count}.csv",
            value_count=count,
            slot_count=slot_count,
            repetitions=repetitions,
            multiplicative_depth=multiplicative_depth,
            plaintext_modulus=plaintext_modulus,
            ring_dimension=ring_dimension,
            output_dir=child,
            overwrite=False,
            _session_factory=_session_factory,
        )
        runs.append({"value_count": count, "status": result["status"], "directory": child.name})
    overall = "PASS" if all(run["status"] == "PASS" for run in runs) else "FAIL"
    summary = {"status": overall, "scheme": "BGV", "operation": "CT+CT", "runs": runs}
    (root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (root / "REPORT.md").write_text(
        "# Synthetic VND BGV CT+CT matrix\n\n"
        + "\n".join(
            f"- Vector length {run['value_count']}: "
            f"[{run['status']}]({run['directory']}/REPORT.md)"
            for run in runs
        )
        + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument(
        "--value-count",
        dest="value_counts",
        nargs="+",
        type=int,
        required=True,
    )
    parser.add_argument("--slot-count", type=int, default=8192)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--multiplicative-depth", type=int, default=1)
    parser.add_argument("--plaintext-modulus", type=int, default=1_000_112_129)
    parser.add_argument("--ring-dimension", type=int, default=16384)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    result = run_add_matrix(
        dataset_dir=args.dataset_dir,
        value_counts=args.value_counts,
        slot_count=args.slot_count,
        repetitions=args.repetitions,
        multiplicative_depth=args.multiplicative_depth,
        plaintext_modulus=args.plaintext_modulus,
        ring_dimension=args.ring_dimension,
        output_dir=args.output_dir,
        overwrite=args.overwrite,
    )
    print(json.dumps(result, indent=2))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
