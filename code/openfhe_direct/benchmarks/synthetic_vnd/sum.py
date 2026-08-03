#!/usr/bin/env python3
"""Benchmark one exact encrypted BGV SUM over a synthetic VND vector."""

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


def _read_values(path: Path, expected_count: int) -> list[int]:
    values: list[int] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if "VALUE" not in set(reader.fieldnames or []):
            raise ValueError(f"dataset is missing VALUE: {path}")
        values.extend(int(row["VALUE"]) for row in reader)
    if len(values) != expected_count:
        raise ValueError(
            f"{path} contains {len(values)} values; expected {expected_count}"
        )
    return values


def _numpy_reference_sum(values: list[int]) -> tuple[int, float]:
    """Time only optimized integer SUM, excluding array preparation."""
    try:
        import numpy as np
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "the SUM plaintext baseline requires NumPy; install it with: "
            "python3 -m pip install numpy"
        ) from error
    array = np.asarray(values, dtype=np.int64)
    started = time.perf_counter()
    result = int(np.sum(array, dtype=np.int64))
    return result, time.perf_counter() - started


def _openfhe_version() -> str:
    try:
        return version("openfhe")
    except PackageNotFoundError:
        return "test-double-or-unavailable"


def _is_prime(value: int) -> bool:
    """Deterministic Miller-Rabin for the supported <=60-bit modulus."""
    if value < 2:
        return False
    for prime in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if value % prime == 0:
            return value == prime
    odd = value - 1
    shifts = 0
    while odd % 2 == 0:
        odd //= 2
        shifts += 1
    for base in (2, 325, 9375, 28178, 450775, 9780504, 1795265022):
        if base % value == 0:
            continue
        witness = pow(base, odd, value)
        if witness in (1, value - 1):
            continue
        for _ in range(shifts - 1):
            witness = witness * witness % value
            if witness == value - 1:
                break
        else:
            return False
    return True


def packed_plaintext_modulus(bits: int, ring_dimension: int) -> int:
    """Return the largest <=bits prime equal to one modulo 2N."""
    if not 3 <= bits <= 60:
        raise ValueError("plaintext_modulus_bits must be between 3 and 60")
    if ring_dimension < 2 or ring_dimension & (ring_dimension - 1):
        raise ValueError("ring_dimension must be a power of two")
    step = 2 * ring_dimension
    candidate = ((2**bits - 2) // step) * step + 1
    while candidate > 2:
        if _is_prime(candidate):
            return candidate
        candidate -= step
    raise ValueError(
        "no packed plaintext modulus exists for this bit count and ring"
    )


def _run_repetition(
    *,
    client: OpenFHEBgvSession,
    evaluator: OpenFHEBgvSession,
    values: list[int],
    repetition: int,
) -> dict[str, Any]:
    expected, numpy_seconds = _numpy_reference_sum(values)

    # HE API call: OpenFHEBgvSession.encrypt(values)
    encrypted, encrypt_seconds = _timed(client.encrypt, values)
    # HE API call: OpenFHEBgvSession.sum(encrypted)
    encrypted_sum, sum_seconds = _timed(evaluator.sum, encrypted)
    # HE API call: OpenFHEBgvSession.decrypt(encrypted_sum)
    observed, decrypt_seconds = _timed(client.decrypt, encrypted_sum)

    absolute_error = abs(int(observed) - expected)
    relative_error = absolute_error / max(1, abs(expected))
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
        "absolute_error_vnd": absolute_error,
        "relative_error": relative_error,
        "status": "PASS" if absolute_error == 0 else "FAIL",
    }


def _write_report(
    root: Path,
    *,
    values: list[int],
    slot_count: int,
    repetitions: int,
    multiplicative_depth: int,
    ring_dimension: int,
    plaintext_modulus: int,
    plaintext_modulus_bits: int,
    expected_sum: int,
    setup_seconds: float,
    rows: list[dict[str, Any]],
) -> None:
    def med(name: str) -> float:
        return median(float(row[name]) for row in rows)

    status = "PASS" if all(row["status"] == "PASS" for row in rows) else "FAIL"
    lines = [
        "# Synthetic VND BGV SUM benchmark",
        "",
        "One integer vector is encrypted, reduced with "
        "`OpenFHEBgvSession.sum()`, and decrypted only for the final audit.",
        "",
        f"- Vector length: `{len(values)}`",
        f"- Slots: `{slot_count}`",
        f"- Observed value range: `{min(values)}` to `{max(values)}` VND",
        f"- Plaintext vector SUM: `{expected_sum}` VND",
        f"- BGV plaintext modulus: `{plaintext_modulus}`",
        f"- Requested plaintext-modulus bits: `{plaintext_modulus_bits}`",
        f"- Maximum positive centered value: `{plaintext_modulus // 2}`",
        f"- Multiplicative depth: `{multiplicative_depth}`",
        f"- Ring dimension: `{ring_dimension}`",
        f"- Repetitions: `{repetitions}`",
        f"- Context/key setup: `{setup_seconds:.9f}` seconds",
        "- Client/evaluator roles: `separated`",
        f"- OpenFHE Python: `{_openfhe_version()}`",
        "- Acceptance: decrypted encrypted SUM must exactly equal NumPy SUM",
        "",
        "| NumPy SUM | Encrypt | HE SUM | HE online | Audit decrypt | "
        "HE SUM / NumPy | Online / NumPy | Absolute error (VND) | Status |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        f"| {med('numpy_sum_seconds'):.9f} | "
        f"{med('encrypt_seconds'):.9f} | "
        f"{med('he_sum_seconds'):.9f} | "
        f"{med('he_online_seconds'):.9f} | "
        f"{med('audit_decrypt_seconds'):.9f} | "
        f"{med('sum_slowdown_vs_numpy'):.2f}x | "
        f"{med('online_slowdown_vs_numpy'):.2f}x | "
        f"{max(int(row['absolute_error_vnd']) for row in rows)} | "
        f"{status} |",
        "",
        "`HE online` includes encryption and encrypted SUM. Setup and final "
        "audit decryption are reported separately. Generated values are "
        "deterministic random integers inside the configured boundaries.",
    ]
    (root / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_sum_count(
    *,
    dataset_path: Path,
    value_count: int,
    slot_count: int,
    repetitions: int,
    multiplicative_depth: int,
    plaintext_modulus_bits: int,
    ring_dimension: int,
    output_dir: Path,
    overwrite: bool,
    _session_factory: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    if value_count > slot_count:
        raise ValueError(
            "this first SUM benchmark requires value_count <= slot_count"
        )
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

    values = _read_values(dataset_path.resolve(), value_count)
    expected_sum = sum(values)
    plaintext_modulus = packed_plaintext_modulus(
        plaintext_modulus_bits,
        ring_dimension,
    )
    if abs(expected_sum) >= plaintext_modulus // 2:
        raise ValueError(
            "plaintext_modulus is unsafe: absolute vector SUM must be below "
            "half the modulus for centered BGV decoding"
        )

    factory = _session_factory or OpenFHEBgvSession
    # HE API call: OpenFHEBgvSession(...) creates context and SUM keys.
    client, setup_seconds = _timed(
        factory,
        slot_count=slot_count,
        plaintext_modulus=plaintext_modulus,
        multiplicative_depth=multiplicative_depth,
        ring_dimension=ring_dimension,
    )
    evaluator = client.evaluator_view()
    rows = [
        _run_repetition(
            client=client,
            evaluator=evaluator,
            values=values,
            repetition=repetition,
        )
        for repetition in range(1, repetitions + 1)
    ]
    status = "PASS" if all(row["status"] == "PASS" for row in rows) else "FAIL"
    summary = {
        "status": status,
        "scheme": "BGV",
        "operation": "encrypted vector SUM",
        "session_method": "sum",
        "value_count": value_count,
        "slot_count": slot_count,
        "plaintext_modulus": plaintext_modulus,
        "plaintext_modulus_bits": plaintext_modulus_bits,
        "multiplicative_depth": multiplicative_depth,
        "centered_capacity": plaintext_modulus // 2,
        "plaintext_vector_sum": expected_sum,
        "setup_seconds": setup_seconds,
        "client_evaluator_separated": True,
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
        values=values,
        slot_count=slot_count,
        repetitions=repetitions,
        multiplicative_depth=multiplicative_depth,
        ring_dimension=ring_dimension,
        plaintext_modulus=plaintext_modulus,
        plaintext_modulus_bits=plaintext_modulus_bits,
        expected_sum=expected_sum,
        setup_seconds=setup_seconds,
        rows=rows,
    )
    return summary


def run_sum_matrix(
    *,
    dataset_dir: Path,
    value_counts: list[int],
    slot_count: int,
    repetitions: int,
    multiplicative_depth: int,
    plaintext_modulus_bits: int,
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
        result = run_sum_count(
            dataset_path=dataset_dir / f"vnd_values_{count}.csv",
            value_count=count,
            slot_count=slot_count,
            repetitions=repetitions,
            multiplicative_depth=multiplicative_depth,
            plaintext_modulus_bits=plaintext_modulus_bits,
            ring_dimension=ring_dimension,
            output_dir=child,
            overwrite=False,
            _session_factory=_session_factory,
        )
        runs.append(
            {
                "value_count": count,
                "plaintext_vector_sum": result["plaintext_vector_sum"],
                "status": result["status"],
                "directory": child.name,
            }
        )
    overall = "PASS" if all(run["status"] == "PASS" for run in runs) else "FAIL"
    summary = {
        "status": overall,
        "scheme": "BGV",
        "operation": "encrypted vector SUM",
        "runs": runs,
    }
    (root / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n",
        encoding="utf-8",
    )
    (root / "REPORT.md").write_text(
        "# Synthetic VND BGV SUM matrix\n\n"
        + "\n".join(
            f"- Vector length {run['value_count']}, plaintext SUM "
            f"{run['plaintext_vector_sum']}: "
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
    parser.add_argument("--multiplicative-depth", type=int, default=0)
    parser.add_argument("--plaintext-modulus-bits", type=int, default=40)
    parser.add_argument("--ring-dimension", type=int, default=16384)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    result = run_sum_matrix(
        dataset_dir=args.dataset_dir,
        value_counts=args.value_counts,
        slot_count=args.slot_count,
        repetitions=args.repetitions,
        multiplicative_depth=args.multiplicative_depth,
        plaintext_modulus_bits=args.plaintext_modulus_bits,
        ring_dimension=args.ring_dimension,
        output_dir=args.output_dir,
        overwrite=args.overwrite,
    )
    print(json.dumps(result, indent=2))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
