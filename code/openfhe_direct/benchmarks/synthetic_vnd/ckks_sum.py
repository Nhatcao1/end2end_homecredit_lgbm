#!/usr/bin/env python3
"""Benchmark normalized CKKS SUM over one synthetic VND vector."""

from __future__ import annotations

import argparse
import csv
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

from code.openfhe_direct import CKKS_CREDIT_PROFILE, OpenFHECreditSession


def _timed(
    function: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> tuple[Any, float]:
    started = time.perf_counter()
    result = function(*args, **kwargs)
    return result, time.perf_counter() - started


def _read_values(path: Path, expected_count: int) -> list[int]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if "VALUE" not in set(reader.fieldnames or []):
            raise ValueError(f"dataset is missing VALUE: {path}")
        values = [int(row["VALUE"]) for row in reader]
    if len(values) != expected_count:
        raise ValueError(
            f"{path} contains {len(values)} values; expected {expected_count}"
        )
    return values


def _numpy_sum(values: list[int]) -> tuple[int, float]:
    try:
        import numpy as np
    except ModuleNotFoundError as error:
        raise RuntimeError("install NumPy with: python3 -m pip install numpy") from error
    array = np.asarray(values, dtype=np.int64)
    started = time.perf_counter()
    result = int(np.sum(array, dtype=np.int64))
    return result, time.perf_counter() - started


def _run_repetition(
    *,
    client: OpenFHECreditSession,
    evaluator: OpenFHECreditSession,
    raw_values: list[int],
    normalization_divisor: float,
    repetition: int,
    absolute_tolerance_vnd: float,
    relative_tolerance: float,
) -> dict[str, Any]:
    expected, numpy_seconds = _numpy_sum(raw_values)
    normalized = [value / normalization_divisor for value in raw_values]
    expected_normalized_sum = expected / normalization_divisor

    # HE API call: OpenFHECreditSession.encrypt(normalized values)
    encrypted, encrypt_seconds = _timed(client.encrypt, normalized)
    # HE API call: OpenFHECreditSession.sum(encrypted)
    encrypted_sum, sum_seconds = _timed(evaluator.sum, encrypted)
    # HE API call: OpenFHECreditSession.decrypt(encrypted_sum)
    normalized_observed, decrypt_seconds = _timed(
        client.decrypt,
        encrypted_sum,
    )
    observed_vnd = float(normalized_observed) * normalization_divisor
    absolute_error = abs(observed_vnd - expected)
    relative_error = absolute_error / max(1.0, abs(expected))
    passed = (
        absolute_error <= absolute_tolerance_vnd
        and relative_error <= relative_tolerance
    )
    online_seconds = encrypt_seconds + sum_seconds
    return {
        "repetition": repetition,
        "numpy_sum_seconds": numpy_seconds,
        "encrypt_seconds": encrypt_seconds,
        "he_sum_seconds": sum_seconds,
        "he_online_seconds": online_seconds,
        "audit_decrypt_seconds": decrypt_seconds,
        "sum_slowdown_vs_numpy": sum_seconds / numpy_seconds,
        "online_slowdown_vs_numpy": online_seconds / numpy_seconds,
        "expected_normalized_sum": expected_normalized_sum,
        "decrypted_normalized_sum": float(normalized_observed),
        "expected_sum_vnd": expected,
        "restored_sum_vnd": observed_vnd,
        "absolute_error_vnd": absolute_error,
        "relative_error": relative_error,
        "status": "PASS" if passed else "FAIL",
    }


def _write_report(
    root: Path,
    *,
    values: list[int],
    expected_sum: int,
    normalization_divisor: float,
    slot_count: int,
    repetitions: int,
    multiplicative_depth: int,
    scaling_mod_size: int,
    first_mod_size: int,
    ring_dimension: int,
    setup_seconds: float,
    rows: list[dict[str, Any]],
) -> None:
    def med(name: str) -> float:
        return median(float(row[name]) for row in rows)

    status = "PASS" if all(row["status"] == "PASS" for row in rows) else "FAIL"
    median_normalized = med("decrypted_normalized_sum")
    median_restored = med("restored_sum_vnd")
    lines = [
        "# Synthetic VND CKKS SUM benchmark",
        "",
        f"- Vector length: `{len(values)}`",
        f"- Observed value range: `{min(values)}` to `{max(values)}` VND",
        f"- Plaintext vector SUM: `{expected_sum}` VND",
        f"- Client normalization divisor: `{normalization_divisor:g}`",
        f"- Normalized value range: `{min(values) / normalization_divisor:g}` "
        f"to `{max(values) / normalization_divisor:g}`",
        f"- Expected normalized SUM: `{expected_sum / normalization_divisor:.12g}`",
        f"- Slots: `{slot_count}`",
        f"- Multiplicative depth: `{multiplicative_depth}`",
        f"- Scaling modulus: `{scaling_mod_size}` bits",
        f"- First modulus: `{first_mod_size}` bits",
        f"- Ring dimension: `{ring_dimension}`",
        f"- Repetitions: `{repetitions}`",
        f"- Context/key setup: `{setup_seconds:.9f}` seconds",
        "- Client/evaluator roles: `separated`",
        "- Acceptance: absolute error and relative error must both pass",
        "",
        "| Expected normalized SUM | Decrypted normalized SUM | "
        "Expected VND SUM | Restored VND SUM | Maximum absolute error (VND) |",
        "|---:|---:|---:|---:|---:|",
        f"| {expected_sum / normalization_divisor:.12g} | "
        f"{median_normalized:.12g} | {expected_sum} | "
        f"{median_restored:.9f} | "
        f"{max(float(row['absolute_error_vnd']) for row in rows):.9f} |",
        "",
        "| NumPy SUM | Encrypt | HE SUM | HE online | Audit decrypt | "
        "HE SUM / NumPy | Online / NumPy | Error (VND) | Relative error | Status |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        f"| {med('numpy_sum_seconds'):.9f} | "
        f"{med('encrypt_seconds'):.9f} | "
        f"{med('he_sum_seconds'):.9f} | "
        f"{med('he_online_seconds'):.9f} | "
        f"{med('audit_decrypt_seconds'):.9f} | "
        f"{med('sum_slowdown_vs_numpy'):.2f}x | "
        f"{med('online_slowdown_vs_numpy'):.2f}x | "
        f"{max(float(row['absolute_error_vnd']) for row in rows):.9f} | "
        f"{max(float(row['relative_error']) for row in rows):.12g} | "
        f"{status} |",
        "",
        "The client divides values before encryption. Only the final audit "
        "multiplies the decrypted scalar by the same divisor to restore VND.",
    ]
    (root / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_ckks_sum_count(
    *,
    dataset_path: Path,
    value_count: int,
    slot_count: int,
    repetitions: int,
    normalization_divisor: float,
    multiplicative_depth: int,
    scaling_mod_size: int,
    first_mod_size: int,
    ring_dimension: int,
    absolute_tolerance_vnd: float,
    relative_tolerance: float,
    output_dir: Path,
    overwrite: bool,
    _session_factory: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    if value_count > slot_count:
        raise ValueError("value_count must not exceed slot_count")
    if normalization_divisor <= 0:
        raise ValueError("normalization_divisor must be positive")
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

    values = _read_values(dataset_path.resolve(), value_count)
    factory = _session_factory or OpenFHECreditSession
    # HE API call: OpenFHECreditSession(...) creates CKKS context and keys.
    client, setup_seconds = _timed(
        factory,
        slot_count=slot_count,
        multiplicative_depth=multiplicative_depth,
        scaling_mod_size=scaling_mod_size,
        first_mod_size=first_mod_size,
        ring_dimension=ring_dimension,
    )
    evaluator = client.evaluator_view()
    rows = [
        _run_repetition(
            client=client,
            evaluator=evaluator,
            raw_values=values,
            normalization_divisor=normalization_divisor,
            repetition=repetition,
            absolute_tolerance_vnd=absolute_tolerance_vnd,
            relative_tolerance=relative_tolerance,
        )
        for repetition in range(1, repetitions + 1)
    ]
    status = "PASS" if all(row["status"] == "PASS" for row in rows) else "FAIL"
    summary = {
        "status": status,
        "scheme": "CKKS",
        "operation": "normalized encrypted vector SUM",
        "value_count": value_count,
        "normalization_divisor": normalization_divisor,
        "plaintext_vector_sum": sum(values),
        "expected_normalized_sum": sum(values) / normalization_divisor,
        "absolute_tolerance_vnd": absolute_tolerance_vnd,
        "relative_tolerance": relative_tolerance,
        "acceptance_rule": "both tolerances must pass",
        "setup_seconds": setup_seconds,
        "client_evaluator_separated": True,
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
        values=values,
        expected_sum=sum(values),
        normalization_divisor=normalization_divisor,
        slot_count=slot_count,
        repetitions=repetitions,
        multiplicative_depth=multiplicative_depth,
        scaling_mod_size=scaling_mod_size,
        first_mod_size=first_mod_size,
        ring_dimension=ring_dimension,
        setup_seconds=setup_seconds,
        rows=rows,
    )
    return summary


def run_ckks_sum_matrix(
    *,
    dataset_dir: Path,
    value_counts: list[int],
    output_dir: Path,
    overwrite: bool,
    **options: Any,
) -> dict[str, Any]:
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
        result = run_ckks_sum_count(
            dataset_path=dataset_dir / f"vnd_values_{count}.csv",
            value_count=count,
            output_dir=child,
            overwrite=False,
            **options,
        )
        runs.append({"value_count": count, "status": result["status"], "directory": child.name})
    status = "PASS" if all(run["status"] == "PASS" for run in runs) else "FAIL"
    summary = {"status": status, "scheme": "CKKS", "runs": runs}
    (root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (root / "REPORT.md").write_text(
        "# Synthetic VND CKKS SUM matrix\n\n"
        + "\n".join(
            f"- Vector length {run['value_count']}: "
            f"[{run['status']}]({run['directory']}/REPORT.md)"
            for run in runs
        )
        + "\n"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--value-count", nargs="+", type=int, required=True)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    result = run_ckks_sum_matrix(
        dataset_dir=args.dataset_dir,
        value_counts=args.value_count,
        slot_count=CKKS_CREDIT_PROFILE.slot_count,
        repetitions=args.repetitions,
        normalization_divisor=CKKS_CREDIT_PROFILE.vnd_normalization_divisor,
        multiplicative_depth=CKKS_CREDIT_PROFILE.benchmark_depth,
        scaling_mod_size=CKKS_CREDIT_PROFILE.scaling_mod_size,
        first_mod_size=CKKS_CREDIT_PROFILE.first_mod_size,
        ring_dimension=CKKS_CREDIT_PROFILE.ring_dimension,
        absolute_tolerance_vnd=CKKS_CREDIT_PROFILE.vnd_absolute_tolerance,
        relative_tolerance=CKKS_CREDIT_PROFILE.vnd_relative_tolerance,
        output_dir=args.output_dir,
        overwrite=args.overwrite,
    )
    print(json.dumps(result, indent=2))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
