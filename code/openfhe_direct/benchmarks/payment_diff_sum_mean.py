#!/usr/bin/env python3
"""Benchmark global PAYMENT_DIFF SUM or MEAN from the raw installments CSV."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
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

from code.openfhe_direct import CKKS_CREDIT_PROFILE, OpenFHECreditSession


OPERATIONS = ("sum", "mean")


@dataclass(frozen=True)
class RawInstallmentParents:
    """Finite parent values selected directly from installments_payments.csv."""

    installment: list[float]
    payment: list[float]
    raw_rows_examined: int
    invalid_rows_dropped: int


def _timed(
    function: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> tuple[Any, float]:
    started = time.perf_counter()
    result = function(*args, **kwargs)
    return result, time.perf_counter() - started


def load_raw_installment_parents(
    installments_csv: Path,
    value_count: int,
) -> RawInstallmentParents:
    """Read the first N finite parent pairs directly from the source CSV."""
    if value_count < 1:
        raise ValueError("value_count must be positive")
    path = installments_csv.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"installments CSV is missing: {path}")

    installment: list[float] = []
    payment: list[float] = []
    raw_rows = invalid_rows = 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"AMT_INSTALMENT", "AMT_PAYMENT"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"installments CSV is missing columns: {sorted(missing)}"
            )
        for row in reader:
            raw_rows += 1
            try:
                due = float(row["AMT_INSTALMENT"])
                paid = float(row["AMT_PAYMENT"])
            except (TypeError, ValueError):
                invalid_rows += 1
                continue
            if not math.isfinite(due) or not math.isfinite(paid):
                invalid_rows += 1
                continue
            installment.append(due)
            payment.append(paid)
            if len(installment) == value_count:
                break
    if len(installment) != value_count:
        raise ValueError(
            f"source contains {len(installment)} valid parent pairs; "
            f"requested {value_count}"
        )
    return RawInstallmentParents(
        installment=installment,
        payment=payment,
        raw_rows_examined=raw_rows,
        invalid_rows_dropped=invalid_rows,
    )


def _python_reference(
    operation: str,
    installment: list[float],
    payment: list[float],
) -> float:
    payment_diff = [
        due - paid
        for due, paid in zip(installment, payment)
    ]
    total = sum(payment_diff)
    if operation == "sum":
        return total
    if operation == "mean":
        return total / len(payment_diff)
    raise ValueError(f"unsupported operation: {operation}")


def _run_repetition(
    *,
    operation: str,
    client: OpenFHECreditSession,
    evaluator: OpenFHECreditSession,
    installment: list[float],
    payment: list[float],
    slot_count: int,
    repetition: int,
    absolute_tolerance: float,
    relative_tolerance: float,
) -> dict[str, Any]:
    reference, python_seconds = _timed(
        _python_reference,
        operation,
        installment,
        payment,
    )
    encrypt_seconds = 0.0
    subtract_seconds = 0.0
    reduction_seconds = 0.0
    weighting_seconds = 0.0
    merge_seconds = 0.0
    global_result_ct: Any | None = None
    ciphertext_chunks = 0
    total_count = len(installment)

    for start in range(0, total_count, slot_count):
        stop = min(start + slot_count, total_count)
        due = installment[start:stop]
        paid = payment[start:stop]
        ciphertext_chunks += 1

        # HE API call: OpenFHECreditSession.encrypt(AMT_INSTALMENT)
        installment_ct, elapsed = _timed(client.encrypt, due)
        encrypt_seconds += elapsed
        # HE API call: OpenFHECreditSession.encrypt(AMT_PAYMENT)
        payment_ct, elapsed = _timed(client.encrypt, paid)
        encrypt_seconds += elapsed
        # HE API call: OpenFHECreditSession.subtract(parent ciphertexts)
        difference_ct, elapsed = _timed(
            evaluator.subtract,
            installment_ct,
            payment_ct,
        )
        subtract_seconds += elapsed

        if operation == "sum":
            # HE API call: OpenFHECreditSession.sum(PAYMENT_DIFF ciphertext)
            partial_ct, elapsed = _timed(evaluator.sum, difference_ct)
            reduction_seconds += elapsed
        else:
            # HE API call: OpenFHECreditSession.mean(PAYMENT_DIFF ciphertext)
            partial_ct, elapsed = _timed(evaluator.mean, difference_ct)
            reduction_seconds += elapsed
            # Weight each encrypted chunk mean by chunk_count / total_count.
            # HE API call: OpenFHECreditSession.multiply_public_scalar(mean_ct)
            partial_ct, elapsed = _timed(
                evaluator.multiply_public_scalar,
                partial_ct,
                len(due) / float(total_count),
            )
            weighting_seconds += elapsed

        if global_result_ct is None:
            global_result_ct = partial_ct
        else:
            # HE API call: OpenFHECreditSession.add(partial scalar ciphertexts)
            global_result_ct, elapsed = _timed(
                evaluator.add,
                global_result_ct,
                partial_ct,
            )
            merge_seconds += elapsed

    if global_result_ct is None:
        raise RuntimeError("no ciphertext chunks were evaluated")
    # HE API call: OpenFHECreditSession.decrypt(final global scalar)
    observed, audit_decrypt_seconds = _timed(
        client.decrypt,
        global_result_ct,
    )
    observed = float(observed)
    absolute_error = abs(observed - reference)
    relative_error = absolute_error / max(1.0, abs(reference))
    passed = (
        absolute_error <= absolute_tolerance
        or relative_error <= relative_tolerance
    )
    calculation_seconds = (
        subtract_seconds
        + reduction_seconds
        + weighting_seconds
        + merge_seconds
    )
    online_seconds = encrypt_seconds + calculation_seconds
    return {
        "operation": operation,
        "repetition": repetition,
        "value_count": total_count,
        "ciphertext_chunks": ciphertext_chunks,
        "python_seconds": python_seconds,
        "encrypt_seconds": encrypt_seconds,
        "subtract_seconds": subtract_seconds,
        "reduction_seconds": reduction_seconds,
        "mean_weighting_seconds": weighting_seconds,
        "encrypted_merge_seconds": merge_seconds,
        "he_calculation_seconds": calculation_seconds,
        "he_online_seconds": online_seconds,
        "audit_decrypt_seconds": audit_decrypt_seconds,
        "python_value": reference,
        "he_value": observed,
        "absolute_error": absolute_error,
        "relative_error": relative_error,
        "calculation_slowdown_vs_python": (
            calculation_seconds / python_seconds
        ),
        "online_slowdown_vs_python": online_seconds / python_seconds,
        "calculation_values_per_second": (
            total_count / calculation_seconds
        ),
        "online_values_per_second": total_count / online_seconds,
        "status": "PASS" if passed else "FAIL",
    }


def _write_report(
    root: Path,
    *,
    operation: str,
    parents: RawInstallmentParents,
    slot_count: int,
    repetitions: int,
    setup_seconds: float,
    input_load_seconds: float,
    rows: list[dict[str, Any]],
) -> None:
    def metric(name: str) -> float:
        return median(float(row[name]) for row in rows)

    status = (
        "PASS"
        if all(row["status"] == "PASS" for row in rows)
        else "FAIL"
    )
    label = operation.upper()
    lines = [
        f"# Direct OpenFHE-Python PAYMENT_DIFF {label}",
        "",
        "The benchmark reads `installments_payments.csv` directly. It "
        "encrypts `AMT_INSTALMENT` and `AMT_PAYMENT`, calculates "
        "`PAYMENT_DIFF = AMT_INSTALMENT - AMT_PAYMENT`, and returns one "
        f"global encrypted {label} across every selected row.",
        "",
        "Parent encryption/final audit stay client-side; a secretless "
        "evaluator calls `OpenFHECreditSession` calculation methods. No HEIR "
        "compiler, generated C++, CMake, prepared-column directory, groupby, "
        "or intermediate decryption.",
        "",
        f"- Valid source rows: `{len(parents.installment)}`",
        f"- Raw rows examined: `{parents.raw_rows_examined}`",
        f"- Invalid parent rows removed: `{parents.invalid_rows_dropped}`",
        f"- Slots per ciphertext: `{slot_count}`",
        f"- Ciphertext chunks: "
        f"`{(len(parents.installment) + slot_count - 1) // slot_count}`",
        f"- Repetitions: `{repetitions}`",
        f"- Direct CSV load/sanitation: `{input_load_seconds:.9f}` seconds",
        f"- Shared context/key setup: `{setup_seconds:.9f}` seconds",
        "",
        "| Python expression | Parent encrypt | CT−CT | Chunk reduction | "
        "Mean weighting | Encrypted merge | HE calculation | HE online | "
        "Audit decrypt | Calc ÷ Python | Online ÷ Python | Max abs. error | "
        "Max relative error | Status |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"
        "---:|---|",
        f"| {metric('python_seconds'):.9f} | "
        f"{metric('encrypt_seconds'):.9f} | "
        f"{metric('subtract_seconds'):.9f} | "
        f"{metric('reduction_seconds'):.9f} | "
        f"{metric('mean_weighting_seconds'):.9f} | "
        f"{metric('encrypted_merge_seconds'):.9f} | "
        f"{metric('he_calculation_seconds'):.9f} | "
        f"{metric('he_online_seconds'):.9f} | "
        f"{metric('audit_decrypt_seconds'):.9f} | "
        f"{metric('calculation_slowdown_vs_python'):.2f}× | "
        f"{metric('online_slowdown_vs_python'):.2f}× | "
        f"{max(float(row['absolute_error']) for row in rows):.12g} | "
        f"{max(float(row['relative_error']) for row in rows):.12g} | "
        f"{status} |",
        "",
        "Python timing covers the matching PAYMENT_DIFF expression and "
        f"global {label}. CSV reading and sanitation are outside both Python "
        "and HE calculation timing. `HE online` includes encryption and every "
        "encrypted calculation but excludes the final audit decryption.",
    ]
    (root / "REPORT.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def run_payment_diff_sum_mean_count(
    *,
    operation: str,
    parents: RawInstallmentParents,
    slot_count: int,
    repetitions: int,
    multiplicative_depth: int,
    scaling_mod_size: int,
    first_mod_size: int,
    ring_dimension: int,
    absolute_tolerance: float,
    relative_tolerance: float,
    output_dir: Path,
    input_load_seconds: float,
    overwrite: bool,
    _session_factory: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    if operation not in OPERATIONS:
        raise ValueError(f"operation must be one of: {OPERATIONS}")
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

    factory = _session_factory or OpenFHECreditSession
    # HE API call: OpenFHECreditSession(...) creates context and keys.
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
            operation=operation,
            client=client,
            evaluator=evaluator,
            installment=parents.installment,
            payment=parents.payment,
            slot_count=slot_count,
            repetition=repetition,
            absolute_tolerance=absolute_tolerance,
            relative_tolerance=relative_tolerance,
        )
        for repetition in range(1, repetitions + 1)
    ]
    status = (
        "PASS"
        if all(row["status"] == "PASS" for row in rows)
        else "FAIL"
    )
    summary = {
        "status": status,
        "backend": "official OpenFHE Python via OpenFHECreditSession",
        "heir_compiler_used": False,
        "source": "raw installments CSV",
        "operation": operation,
        "value_count": len(parents.installment),
        "slot_count": slot_count,
        "ciphertext_chunks": rows[0]["ciphertext_chunks"],
        "repetitions": repetitions,
        "input_load_seconds": input_load_seconds,
        "raw_rows_examined": parents.raw_rows_examined,
        "invalid_rows_dropped": parents.invalid_rows_dropped,
        "setup_seconds": setup_seconds,
        "client_evaluator_separated": True,
    }
    with (root / "results.csv").open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (root / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_report(
        root,
        operation=operation,
        parents=parents,
        slot_count=slot_count,
        repetitions=repetitions,
        setup_seconds=setup_seconds,
        input_load_seconds=input_load_seconds,
        rows=rows,
    )
    return summary


def run_payment_diff_sum_mean_matrix(
    *,
    operation: str,
    installments_csv: Path,
    value_counts: list[int],
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
    if operation not in OPERATIONS:
        raise ValueError(f"operation must be one of: {OPERATIONS}")
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
    all_passed = True
    for value_count in value_counts:
        # Each row-count test independently reads the raw installments source.
        parents, load_seconds = _timed(
            load_raw_installment_parents,
            installments_csv,
            value_count,
        )
        child = root / f"rows_{value_count}"
        result = run_payment_diff_sum_mean_count(
            operation=operation,
            parents=parents,
            slot_count=slot_count,
            repetitions=repetitions,
            multiplicative_depth=multiplicative_depth,
            scaling_mod_size=scaling_mod_size,
            first_mod_size=first_mod_size,
            ring_dimension=ring_dimension,
            absolute_tolerance=absolute_tolerance,
            relative_tolerance=relative_tolerance,
            output_dir=child,
            input_load_seconds=load_seconds,
            overwrite=False,
            _session_factory=_session_factory,
        )
        all_passed = all_passed and result["status"] == "PASS"
        runs.append(
            {
                "value_count": value_count,
                "status": result["status"],
                "directory": child.name,
            }
        )
    summary = {
        "status": "PASS" if all_passed else "FAIL",
        "backend": "official OpenFHE Python via OpenFHECreditSession",
        "heir_compiler_used": False,
        "source_csv": str(installments_csv.resolve()),
        "operation": operation,
        "value_counts": value_counts,
        "runs": runs,
    }
    (root / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n",
        encoding="utf-8",
    )
    (root / "REPORT.md").write_text(
        f"# Raw-installments PAYMENT_DIFF {operation.upper()} matrix\n\n"
        "Each row-count run reads parent values selected directly from the "
        "source CSV and returns one encrypted global result across all "
        "ciphertext chunks.\n\n"
        + "\n".join(
            f"- {run['value_count']} values: "
            f"[{run['status']}]({run['directory']}/REPORT.md)"
            for run in runs
        )
        + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation", choices=OPERATIONS, required=True)
    parser.add_argument("--installments", type=Path, required=True)
    parser.add_argument(
        "--value-count",
        dest="value_counts",
        nargs="+",
        type=int,
        required=True,
    )
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    result = run_payment_diff_sum_mean_matrix(
        operation=args.operation,
        installments_csv=args.installments,
        value_counts=args.value_counts,
        slot_count=CKKS_CREDIT_PROFILE.slot_count,
        repetitions=args.repetitions,
        multiplicative_depth=CKKS_CREDIT_PROFILE.benchmark_depth,
        scaling_mod_size=CKKS_CREDIT_PROFILE.scaling_mod_size,
        first_mod_size=CKKS_CREDIT_PROFILE.first_mod_size,
        ring_dimension=CKKS_CREDIT_PROFILE.ring_dimension,
        absolute_tolerance=CKKS_CREDIT_PROFILE.absolute_tolerance,
        relative_tolerance=CKKS_CREDIT_PROFILE.relative_tolerance,
        output_dir=args.output_dir,
        overwrite=args.overwrite,
    )
    print(json.dumps(result, indent=2))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
