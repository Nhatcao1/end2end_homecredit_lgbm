#!/usr/bin/env python3
"""Run one direct OpenFHE-Python API function over prepared row batches."""

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

from code.openfhe_direct import OpenFHECreditSession
from code.openfhe_direct.benchmarks.api_latency import (
    _decrypt_result,
    _error_values,
    _expected_values,
    _timed,
)
from code.openfhe_direct.prepared_data import (
    PreparedPaymentGroup,
    load_prepared_parent_columns,
    public_power_of_two_scale,
)


FUNCTIONS = (
    "encrypt",
    "decrypt",
    "add",
    "subtract",
    "multiply",
    "add_public_scalar",
    "add_public_vector",
    "multiply_public_scalar",
    "multiply_public_vector",
    "square",
    "sum",
    "mean",
    "minimum",
    "maximum",
    "variance_components",
    "variance",
    "covariance_components",
    "correlation_components",
    "weighted_sum",
    "risk_score",
)

DIFFERENCE_INPUT_FUNCTIONS = {
    "add_public_scalar",
    "multiply_public_scalar",
    "multiply_public_vector",
    "square",
    "sum",
    "mean",
    "variance_components",
    "variance",
    "weighted_sum",
    "risk_score",
}


def _chunks(
    installment: list[float],
    payment: list[float],
    slot_count: int,
) -> list[PreparedPaymentGroup]:
    result: list[PreparedPaymentGroup] = []
    for index, start in enumerate(range(0, len(installment), slot_count)):
        stop = min(start + slot_count, len(installment))
        result.append(
            PreparedPaymentGroup(
                applicant_id=f"batch_{index:06d}",
                slot_count=slot_count,
                installment=installment[start:stop],
                payment=payment[start:stop],
            )
        )
    return result


def _call_function(
    function: str,
    session: OpenFHECreditSession,
    group: PreparedPaymentGroup,
    installment_ct: Any,
    payment_ct: Any,
    difference_ct: Any,
) -> Any:
    """Call exactly one named public API method."""
    count = len(group.installment)
    weights = [1.0 / count] * count
    if function == "decrypt":
        # HE API call: OpenFHECreditSession.decrypt(installment_ct)
        return session.decrypt(installment_ct)
    if function == "add":
        # HE API call: OpenFHECreditSession.add(parent ciphertexts)
        return session.add(installment_ct, payment_ct)
    if function == "subtract":
        # HE API call: OpenFHECreditSession.subtract(parent ciphertexts)
        return session.subtract(installment_ct, payment_ct)
    if function == "multiply":
        # HE API call: OpenFHECreditSession.multiply(parent ciphertexts)
        return session.multiply(installment_ct, payment_ct)
    if function == "add_public_scalar":
        # HE API call: OpenFHECreditSession.add_public_scalar(diff_ct, 1.5)
        return session.add_public_scalar(difference_ct, 1.5)
    if function == "add_public_vector":
        # HE API call: OpenFHECreditSession.add_public_vector(payment_ct, tens)
        return session.add_public_vector(payment_ct, [10.0] * count)
    if function == "multiply_public_scalar":
        # HE API call: OpenFHECreditSession.multiply_public_scalar(diff_ct, 0.5)
        return session.multiply_public_scalar(difference_ct, 0.5)
    if function == "multiply_public_vector":
        # HE API call: OpenFHECreditSession.multiply_public_vector(diff_ct, weights)
        return session.multiply_public_vector(difference_ct, weights)
    if function == "square":
        # HE API call: OpenFHECreditSession.square(difference_ct)
        return session.square(difference_ct)
    if function == "sum":
        # HE API call: OpenFHECreditSession.sum(difference_ct)
        return session.sum(difference_ct)
    if function == "mean":
        # HE API call: OpenFHECreditSession.mean(difference_ct)
        return session.mean(difference_ct)
    if function == "minimum":
        # HE API call: OpenFHECreditSession.minimum(installment_ct)
        return session.minimum(installment_ct)
    if function == "maximum":
        # HE API call: OpenFHECreditSession.maximum(installment_ct)
        return session.maximum(installment_ct)
    if function == "variance_components":
        # HE API call: OpenFHECreditSession.variance_components(diff_ct)
        return session.variance_components(difference_ct)
    if function == "variance":
        # HE API call: OpenFHECreditSession.variance(difference_ct)
        return session.variance(difference_ct)
    if function == "covariance_components":
        # HE API call: OpenFHECreditSession.covariance_components(parents)
        return session.covariance_components(
            installment_ct,
            payment_ct,
        )
    if function == "correlation_components":
        # HE API call: OpenFHECreditSession.correlation_components(parents)
        return session.correlation_components(
            installment_ct,
            payment_ct,
        )
    if function == "weighted_sum":
        # HE API call: OpenFHECreditSession.weighted_sum(diff_ct, weights)
        return session.weighted_sum(difference_ct, weights)
    if function == "risk_score":
        # HE API call: OpenFHECreditSession.risk_score(diff_ct, weights, bias)
        return session.risk_score(difference_ct, weights, 1.5)
    raise ValueError(f"unsupported function: {function}")


def _run_repetition(
    *,
    function: str,
    session: OpenFHECreditSession,
    groups: list[PreparedPaymentGroup],
    absolute_tolerance: float,
    relative_tolerance: float,
) -> dict[str, Any]:
    parent_encrypt_seconds = 0.0
    prerequisite_seconds = 0.0
    function_seconds = 0.0
    audit_seconds = 0.0
    max_absolute = 0.0
    max_relative = 0.0

    for group in groups:
        expected = _expected_values(group)[function]
        if function == "encrypt":
            # HE API call: OpenFHECreditSession.encrypt(AMT_INSTALMENT)
            result, elapsed = _timed(session.encrypt, group.installment)
            function_seconds += elapsed
            observed, elapsed = _timed(_decrypt_result, session, result)
            audit_seconds += elapsed
        else:
            installment_ct = None
            payment_ct = None
            if function != "add_public_vector":
                # HE API call: OpenFHECreditSession.encrypt(AMT_INSTALMENT)
                installment_ct, elapsed = _timed(
                    session.encrypt,
                    group.installment,
                )
                parent_encrypt_seconds += elapsed
            if function not in {"decrypt", "minimum", "maximum"}:
                # HE API call: OpenFHECreditSession.encrypt(AMT_PAYMENT)
                payment_ct, elapsed = _timed(
                    session.encrypt,
                    group.payment,
                )
                parent_encrypt_seconds += elapsed
            difference_ct = None
            if function in DIFFERENCE_INPUT_FUNCTIONS:
                # HE API call: OpenFHECreditSession.subtract(parent ciphertexts)
                difference_ct, elapsed = _timed(
                    session.subtract,
                    installment_ct,
                    payment_ct,
                )
                prerequisite_seconds += elapsed
            result, elapsed = _timed(
                _call_function,
                function,
                session,
                group,
                installment_ct,
                payment_ct,
                difference_ct,
            )
            function_seconds += elapsed
            if function == "decrypt":
                observed = result
            else:
                observed, elapsed = _timed(
                    _decrypt_result,
                    session,
                    result,
                )
                audit_seconds += elapsed

        errors = _error_values(observed, expected)
        max_absolute = max(
            max_absolute,
            max(error[0] for error in errors),
        )
        max_relative = max(
            max_relative,
            max(error[1] for error in errors),
        )

    passed = (
        max_absolute <= absolute_tolerance
        or max_relative <= relative_tolerance
    )
    return {
        "parent_encrypt_seconds": parent_encrypt_seconds,
        "prerequisite_seconds": prerequisite_seconds,
        "function_seconds": function_seconds,
        "audit_seconds": audit_seconds,
        "max_abs_error": max_absolute,
        "max_relative_error": max_relative,
        "status": "PASS" if passed else "FAIL",
    }


def _write_report(
    root: Path,
    *,
    function: str,
    value_count: int,
    slot_count: int,
    chunk_count: int,
    repetitions: int,
    setup_seconds: float,
    input_scale: float | None,
    summary: dict[str, Any],
) -> None:
    lines = [
        f"# OpenFHE-Python batch: `{function}`",
        "",
        "This command runs one public `OpenFHECreditSession` method only. "
        "Rows are streamed as independent packed ciphertext chunks.",
        "",
        f"- Values: `{value_count}`",
        f"- Slots per ciphertext: `{slot_count}`",
        f"- Ciphertext chunks: `{chunk_count}`",
        f"- Repetitions: `{repetitions}`",
        f"- Shared setup/key generation: `{setup_seconds:.9f}` seconds",
        *(
            [
                f"- CKKS/FHEW public input scale: `{input_scale:g}`",
                "- MIN/MAX source: encrypted `AMT_INSTALMENT` vector",
            ]
            if input_scale is not None
            else []
        ),
        "",
        "| Parent encryption | Derived-input prerequisite | Function latency | "
        "Audit decryption | Max abs. error | Max relative error | Status |",
        "|---:|---:|---:|---:|---:|---:|---|",
        f"| {summary['parent_encrypt_seconds']:.9f} | "
        f"{summary['prerequisite_seconds']:.9f} | "
        f"{summary['function_seconds']:.9f} | "
        f"{summary['audit_seconds']:.9f} | "
        f"{summary['max_abs_error']:.12g} | "
        f"{summary['max_relative_error']:.12g} | "
        f"{summary['status']} |",
        "",
        "The latency values are medians of complete passes over all chunks. "
        "Audit decryption is excluded from function latency. Reductions are "
        "evaluated independently inside each ciphertext chunk; this runner "
        "does not merge chunks into a global aggregate.",
    ]
    (root / "REPORT.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def run_batch_benchmark(
    *,
    function: str,
    prepared_dir: Path,
    value_count: int,
    slot_count: int,
    repetitions: int,
    multiplicative_depth: int,
    ring_dimension: int,
    absolute_tolerance: float,
    relative_tolerance: float,
    output_dir: Path,
    overwrite: bool,
    _openfhe_module: Any | None = None,
    _session_factory: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    if function not in FUNCTIONS:
        raise ValueError(f"unsupported function: {function}")
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
    groups = _chunks(
        parents.installment,
        parents.payment,
        slot_count,
    )
    if function == "variance" and len(groups[-1].installment) < 2:
        raise ValueError(
            "sample variance cannot run on a final one-value chunk; "
            "change value_count or slot_count"
        )
    minmax = function in {"minimum", "maximum"}
    input_scale = (
        public_power_of_two_scale(parents.installment)
        if minmax
        else None
    )
    factory = _session_factory or OpenFHECreditSession
    session_options: dict[str, Any] = {
        "slot_count": slot_count,
        "multiplicative_depth": multiplicative_depth,
        "ring_dimension": ring_dimension,
    }
    if minmax:
        session_options.update(
            input_scale=input_scale,
            enable_minmax=True,
        )
    else:
        session_options["_openfhe_module"] = _openfhe_module
    # HE API call: OpenFHECreditSession(...) creates context and keys.
    session, setup_seconds = _timed(
        factory,
        **session_options,
    )

    rows = [
        {
            "repetition": repetition,
            **_run_repetition(
                function=function,
                session=session,
                groups=groups,
                absolute_tolerance=absolute_tolerance,
                relative_tolerance=relative_tolerance,
            ),
        }
        for repetition in range(1, repetitions + 1)
    ]
    metric_names = (
        "parent_encrypt_seconds",
        "prerequisite_seconds",
        "function_seconds",
        "audit_seconds",
    )
    medians = {
        name: median(float(row[name]) for row in rows)
        for name in metric_names
    }
    summary = {
        "status": (
            "PASS"
            if all(row["status"] == "PASS" for row in rows)
            else "FAIL"
        ),
        "backend": "official OpenFHE Python",
        "function": function,
        "value_count": value_count,
        "slot_count": slot_count,
        "ciphertext_chunks": len(groups),
        "repetitions": repetitions,
        "setup_seconds": setup_seconds,
        "input_scale": input_scale,
        **medians,
        "max_abs_error": max(float(row["max_abs_error"]) for row in rows),
        "max_relative_error": max(
            float(row["max_relative_error"]) for row in rows
        ),
        "prepared_files_used": parents.files_used,
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
        function=function,
        value_count=value_count,
        slot_count=slot_count,
        chunk_count=len(groups),
        repetitions=repetitions,
        setup_seconds=setup_seconds,
        input_scale=input_scale,
        summary=summary,
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--function", choices=FUNCTIONS, required=True)
    parser.add_argument(
        "--prepared-dir",
        type=Path,
        default=Path("data/prepared/installments_columns"),
    )
    parser.add_argument("--value-count", type=int, required=True)
    parser.add_argument("--slot-count", type=int, default=8192)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--multiplicative-depth", type=int, default=4)
    parser.add_argument("--ring-dimension", type=int, default=0)
    parser.add_argument("--absolute-tolerance", type=float, default=1e-6)
    parser.add_argument("--relative-tolerance", type=float, default=1e-5)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    result = run_batch_benchmark(
        function=args.function,
        prepared_dir=args.prepared_dir,
        value_count=args.value_count,
        slot_count=args.slot_count,
        repetitions=args.repetitions,
        multiplicative_depth=args.multiplicative_depth,
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
