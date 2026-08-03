#!/usr/bin/env python3
"""Benchmark primitive arithmetic through ``OpenFHECreditSession`` only."""

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


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from code.openfhe_direct import CKKS_CREDIT_PROFILE, OpenFHECreditSession
from code.openfhe_direct.prepared_data import (
    PreparedPaymentGroup,
    load_prepared_parent_columns,
)


OPERATIONS = (
    "add",
    "subtract",
    "multiply",
    "add_public_vector",
    "multiply_public_vector",
)
LABELS = {
    "add": "CT+CT",
    "subtract": "CT-CT",
    "multiply": "CT×CT",
    "add_public_vector": "CT+PT",
    "multiply_public_vector": "CT×PT",
}


def _timed(
    function: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> tuple[Any, float]:
    started = time.perf_counter()
    result = function(*args, **kwargs)
    return result, time.perf_counter() - started


def _chunks(
    installment: list[float],
    payment: list[float],
    slot_count: int,
) -> list[PreparedPaymentGroup]:
    return [
        PreparedPaymentGroup(
            applicant_id=f"chunk_{index:06d}",
            slot_count=slot_count,
            installment=installment[start : start + slot_count],
            payment=payment[start : start + slot_count],
        )
        for index, start in enumerate(
            range(0, len(installment), slot_count)
        )
    ]


def _python_values(
    operation: str,
    installment: list[float],
    payment: list[float],
) -> list[float]:
    if operation in ("add", "add_public_vector"):
        return [
            due + paid
            for due, paid in zip(installment, payment)
        ]
    if operation == "subtract":
        return [
            due - paid
            for due, paid in zip(installment, payment)
        ]
    if operation in ("multiply", "multiply_public_vector"):
        return [
            due * paid
            for due, paid in zip(installment, payment)
        ]
    raise ValueError(f"unsupported primitive operation: {operation}")


def _evaluate_chunk(
    *,
    operation: str,
    client: OpenFHECreditSession,
    evaluator: OpenFHECreditSession,
    group: PreparedPaymentGroup,
) -> tuple[list[float], float, float, float]:
    encryption_seconds = evaluation_seconds = decrypt_seconds = 0.0

    # HE API call: OpenFHECreditSession.encrypt(AMT_INSTALMENT)
    installment_ct, elapsed = _timed(
        client.encrypt,
        group.installment,
    )
    encryption_seconds += elapsed

    if operation in ("add", "subtract", "multiply"):
        # HE API call: OpenFHECreditSession.encrypt(AMT_PAYMENT)
        payment_ct, elapsed = _timed(
            client.encrypt,
            group.payment,
        )
        encryption_seconds += elapsed
        if operation == "add":
            # HE API call: OpenFHECreditSession.add(parent ciphertexts)
            result_ct, evaluation_seconds = _timed(
                evaluator.add,
                installment_ct,
                payment_ct,
            )
        elif operation == "subtract":
            # HE API call: OpenFHECreditSession.subtract(parent ciphertexts)
            result_ct, evaluation_seconds = _timed(
                evaluator.subtract,
                installment_ct,
                payment_ct,
            )
        else:
            # HE API call: OpenFHECreditSession.multiply(parent ciphertexts)
            result_ct, evaluation_seconds = _timed(
                evaluator.multiply,
                installment_ct,
                payment_ct,
            )
    elif operation == "add_public_vector":
        # HE API call: OpenFHECreditSession.add_public_vector(CT, PT)
        result_ct, evaluation_seconds = _timed(
            evaluator.add_public_vector,
            installment_ct,
            group.payment,
        )
    elif operation == "multiply_public_vector":
        # HE API call: OpenFHECreditSession.multiply_public_vector(CT, PT)
        result_ct, evaluation_seconds = _timed(
            evaluator.multiply_public_vector,
            installment_ct,
            group.payment,
        )
    else:
        raise ValueError(f"unsupported primitive operation: {operation}")

    # HE API call: OpenFHECreditSession.decrypt(final result)
    observed, decrypt_seconds = _timed(client.decrypt, result_ct)
    if not isinstance(observed, list):
        raise TypeError("primitive operation must decrypt to a vector")
    return (
        observed,
        encryption_seconds,
        evaluation_seconds,
        decrypt_seconds,
    )


def _run_operation(
    *,
    operation: str,
    client: OpenFHECreditSession,
    evaluator: OpenFHECreditSession,
    groups: list[PreparedPaymentGroup],
    installment: list[float],
    payment: list[float],
    repetition: int,
    absolute_tolerance: float,
    relative_tolerance: float,
) -> dict[str, Any]:
    expected, python_seconds = _timed(
        _python_values,
        operation,
        installment,
        payment,
    )
    observed: list[float] = []
    encryption_seconds = evaluation_seconds = decrypt_seconds = 0.0
    for group in groups:
        (
            chunk_values,
            chunk_encrypt,
            chunk_evaluate,
            chunk_decrypt,
        ) = _evaluate_chunk(
            operation=operation,
            client=client,
            evaluator=evaluator,
            group=group,
        )
        observed.extend(chunk_values)
        encryption_seconds += chunk_encrypt
        evaluation_seconds += chunk_evaluate
        decrypt_seconds += chunk_decrypt

    if len(observed) != len(expected):
        raise RuntimeError("decrypted result length does not match input")
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
    passed = (
        maximum_absolute <= absolute_tolerance
        or maximum_relative <= relative_tolerance
    )
    online_seconds = (
        encryption_seconds + evaluation_seconds + decrypt_seconds
    )
    return {
        "operation": LABELS[operation],
        "session_method": operation,
        "repetition": repetition,
        "python_seconds": python_seconds,
        "encrypt_seconds": encryption_seconds,
        "evaluate_seconds": evaluation_seconds,
        "audit_decrypt_seconds": decrypt_seconds,
        "he_online_seconds": online_seconds,
        "evaluation_slowdown_vs_python": (
            evaluation_seconds / python_seconds
        ),
        "online_slowdown_vs_python": online_seconds / python_seconds,
        "evaluation_values_per_second": (
            len(expected) / evaluation_seconds
        ),
        "online_values_per_second": len(expected) / online_seconds,
        "max_abs_error": maximum_absolute,
        "max_relative_error": maximum_relative,
        "status": "PASS" if passed else "FAIL",
    }


def _write_count_report(
    root: Path,
    *,
    value_count: int,
    slot_count: int,
    setup_seconds: float,
    repetitions: int,
    rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# Direct OpenFHE-Python primitive arithmetic",
        "",
        "The remote client encrypts and decrypts. A secretless evaluator "
        "view calls public methods of `OpenFHECreditSession`. "
        "There is no HEIR lowering, generated MLIR/C++, or CMake build.",
        "",
        f"- Real installment rows: `{value_count}`",
        f"- Slots per ciphertext: `{slot_count}`",
        f"- Ciphertext chunks: `{(value_count + slot_count - 1) // slot_count}`",
        f"- Repetitions: `{repetitions}`",
        f"- Shared OpenFHE context/key setup: `{setup_seconds:.9f}` seconds",
        "",
        "Each operation is isolated: it receives fresh parent encryption and "
        "does not consume another operation's output. The context and keys "
        "are shared for this row-count run.",
        "",
        "| Operation | Session method | Python median (s) | Encrypt median "
        "(s) | HE evaluation median (s) | Audit decrypt median (s) | HE "
        "online median (s) | Eval ÷ Python | Online ÷ Python | Max abs. "
        "error | Max relative error | Eval values/s | Online values/s | "
        "Status |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"
        "---:|---|",
    ]
    for operation in OPERATIONS:
        selected = [
            row for row in rows if row["session_method"] == operation
        ]
        if not selected:
            continue

        def metric(name: str) -> float:
            return median(float(row[name]) for row in selected)

        status = (
            "PASS"
            if all(row["status"] == "PASS" for row in selected)
            else "FAIL"
        )
        lines.append(
            f"| {LABELS[operation]} | `{operation}` | "
            f"{metric('python_seconds'):.9f} | "
            f"{metric('encrypt_seconds'):.9f} | "
            f"{metric('evaluate_seconds'):.9f} | "
            f"{metric('audit_decrypt_seconds'):.9f} | "
            f"{metric('he_online_seconds'):.9f} | "
            f"{metric('evaluation_slowdown_vs_python'):.2f}× | "
            f"{metric('online_slowdown_vs_python'):.2f}× | "
            f"{max(float(row['max_abs_error']) for row in selected):.12g} | "
            f"{max(float(row['max_relative_error']) for row in selected):.12g} | "
            f"{metric('evaluation_values_per_second'):.2f} | "
            f"{metric('online_values_per_second'):.2f} | "
            f"{status} |"
        )
    lines.extend(
        [
            "",
            "`HE online` is encryption + the named session method + final "
            "audit decryption. Context/key setup and prepared CSV loading are "
            "reported separately and excluded from both Python and HE timing.",
        ]
    )
    (root / "REPORT.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def run_primitive_count(
    *,
    prepared_dir: Path,
    value_count: int,
    operations: tuple[str, ...],
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
    if value_count < 1:
        raise ValueError("value_count must be positive")
    if not operations or any(operation not in OPERATIONS for operation in operations):
        raise ValueError(f"operations must be selected from {OPERATIONS}")
    if len(set(operations)) != len(operations):
        raise ValueError("operations must be unique")
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

    parents = load_prepared_parent_columns(
        prepared_dir.resolve(),
        value_count,
    )
    groups = _chunks(parents.installment, parents.payment, slot_count)
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
    # Evaluator receives the compatible context/evaluation keys, but neither
    # the public encryption key nor the client secret key.
    evaluator = client.evaluator_view()

    rows = [
        _run_operation(
            operation=operation,
            client=client,
            evaluator=evaluator,
            groups=groups,
            installment=parents.installment,
            payment=parents.payment,
            repetition=repetition,
            absolute_tolerance=absolute_tolerance,
            relative_tolerance=relative_tolerance,
        )
        for operation in operations
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
        "value_count": value_count,
        "slot_count": slot_count,
        "ciphertext_chunks": len(groups),
        "operations": [LABELS[operation] for operation in operations],
        "session_methods": list(operations),
        "repetitions": repetitions,
        "setup_seconds": setup_seconds,
        "context": {
            "multiplicative_depth": multiplicative_depth,
            "scaling_mod_size": scaling_mod_size,
            "first_mod_size": first_mod_size,
            "ring_dimension": ring_dimension or "OpenFHE-selected",
        },
        "prepared_files_used": parents.files_used,
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
    _write_count_report(
        root,
        value_count=value_count,
        slot_count=slot_count,
        setup_seconds=setup_seconds,
        repetitions=repetitions,
        rows=rows,
    )
    return summary


def run_primitive_matrix(
    *,
    prepared_dir: Path,
    value_counts: list[int],
    operations: tuple[str, ...],
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
        child = root / f"rows_{value_count}"
        result = run_primitive_count(
            prepared_dir=prepared_dir,
            value_count=value_count,
            operations=operations,
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
        "value_counts": value_counts,
        "operations": [LABELS[operation] for operation in operations],
        "runs": runs,
    }
    (root / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n",
        encoding="utf-8",
    )
    (root / "REPORT.md").write_text(
        "# Direct OpenFHE-Python primitive matrix\n\n"
        "No HEIR kernels are generated or loaded. Each row-count run creates "
        "one `OpenFHECreditSession`, then calls its public arithmetic methods "
        "sequentially and independently.\n\n"
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
    parser.add_argument(
        "--prepared-dir",
        type=Path,
        default=Path("data/prepared/installments_columns"),
    )
    parser.add_argument(
        "--value-count",
        dest="value_counts",
        nargs="+",
        type=int,
        required=True,
    )
    parser.add_argument(
        "--operation",
        dest="operations",
        nargs="+",
        choices=OPERATIONS,
        default=list(OPERATIONS),
    )
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    result = run_primitive_matrix(
        prepared_dir=args.prepared_dir,
        value_counts=args.value_counts,
        operations=tuple(args.operations),
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
