#!/usr/bin/env python3
"""Time the direct OpenFHE-Python credit API, one public method at a time."""

from __future__ import annotations

import argparse
import csv
from dataclasses import fields, is_dataclass
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

from code.openfhe_direct import (
    EncryptedScalar,
    EncryptedVector,
    OpenFHECreditSession,
)
from code.openfhe_direct.prepared_data import (
    PreparedPaymentGroup,
    load_prepared_group,
)


def _timed(
    function: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> tuple[Any, float]:
    started = time.perf_counter()
    result = function(*args, **kwargs)
    return result, time.perf_counter() - started


def _decrypt_result(session: OpenFHECreditSession, value: Any) -> Any:
    """Recursively decrypt one API result for the final accuracy audit."""
    if isinstance(value, (EncryptedVector, EncryptedScalar)):
        # HE API call: OpenFHECreditSession.decrypt(value)
        return session.decrypt(value)
    if isinstance(value, dict):
        return {
            key: _decrypt_result(session, item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _decrypt_result(session, item)
            for item in value
        ]
    if isinstance(value, (int, float)):
        return value
    if is_dataclass(value):
        return {
            field.name: _decrypt_result(session, getattr(value, field.name))
            for field in fields(value)
        }
    raise TypeError(f"unsupported benchmark result: {type(value).__name__}")


def _error_values(observed: Any, expected: Any) -> list[tuple[float, float]]:
    """Return absolute/relative error pairs for matching result structures."""
    if isinstance(expected, dict):
        if not isinstance(observed, dict) or observed.keys() != expected.keys():
            raise ValueError("observed and expected result fields differ")
        result: list[tuple[float, float]] = []
        for key in expected:
            result.extend(_error_values(observed[key], expected[key]))
        return result
    if isinstance(expected, list):
        if not isinstance(observed, list) or len(observed) != len(expected):
            raise ValueError("observed and expected vector lengths differ")
        result = []
        for actual_value, expected_value in zip(observed, expected):
            result.extend(_error_values(actual_value, expected_value))
        return result
    actual_number = float(observed)
    expected_number = float(expected)
    absolute = abs(actual_number - expected_number)
    relative = absolute / max(1.0, abs(expected_number))
    return [(absolute, relative)]


def _expected_values(group: PreparedPaymentGroup) -> dict[str, Any]:
    installment = group.installment
    payment = group.payment
    difference = [due - paid for due, paid in zip(installment, payment)]
    count = len(difference)
    weights = [1.0 / count] * count
    sum_difference = sum(difference)
    sum_difference2 = sum(value * value for value in difference)
    sum_installment = sum(installment)
    sum_payment = sum(payment)
    sum_product = sum(
        due * paid for due, paid in zip(installment, payment)
    )
    return {
        "encrypt": installment,
        "decrypt": installment,
        "add": [due + paid for due, paid in zip(installment, payment)],
        "subtract": difference,
        "multiply": [
            due * paid for due, paid in zip(installment, payment)
        ],
        "add_public_scalar": [value + 1.5 for value in difference],
        "add_public_vector": [value + 10.0 for value in payment],
        "multiply_public_scalar": [value * 0.5 for value in difference],
        "multiply_public_vector": [
            value * weight for value, weight in zip(difference, weights)
        ],
        "square": [value * value for value in difference],
        "sum": sum_difference,
        "mean": sum_difference / count,
        "minimum": min(installment),
        "maximum": max(installment),
        "variance_components": {
            "sum_x": sum_difference,
            "sum_x2": sum_difference2,
        },
        "variance": (
            sum(
                (value - sum_difference / count) ** 2
                for value in difference
            )
            / (count - 1)
            if count > 1
            else None
        ),
        "covariance_components": {
            "sum_x": sum_installment,
            "sum_y": sum_payment,
            "sum_xy": sum_product,
        },
        "correlation_components": {
            "sum_x": sum_installment,
            "sum_y": sum_payment,
            "sum_x2": sum(value * value for value in installment),
            "sum_y2": sum(value * value for value in payment),
            "sum_xy": sum_product,
        },
        "weighted_sum": sum(
            value * weight for value, weight in zip(difference, weights)
        ),
        "risk_score": (
            sum(
                value * weight
                for value, weight in zip(difference, weights)
            )
            + 1.5
        ),
    }


def _operation_calls(
    session: OpenFHECreditSession,
    group: PreparedPaymentGroup,
    installment_ct: EncryptedVector,
    payment_ct: EncryptedVector,
    difference_ct: EncryptedVector,
) -> dict[str, Callable[[], Any]]:
    count = len(group.installment)
    weights = [1.0 / count] * count
    tens = [10.0] * count
    return {
        # HE API call: OpenFHECreditSession.encrypt(installment)
        "encrypt": lambda: session.encrypt(group.installment),
        # HE API call: OpenFHECreditSession.decrypt(installment_ct)
        "decrypt": lambda: session.decrypt(installment_ct),
        # HE API call: OpenFHECreditSession.add(installment_ct, payment_ct)
        "add": lambda: session.add(installment_ct, payment_ct),
        # HE API call: OpenFHECreditSession.subtract(parents)
        "subtract": lambda: session.subtract(installment_ct, payment_ct),
        # HE API call: OpenFHECreditSession.multiply(parents)
        "multiply": lambda: session.multiply(installment_ct, payment_ct),
        # HE API call: OpenFHECreditSession.add_public_scalar(diff_ct, 1.5)
        "add_public_scalar": lambda: session.add_public_scalar(
            difference_ct,
            1.5,
        ),
        # HE API call: OpenFHECreditSession.add_public_vector(payment_ct, tens)
        "add_public_vector": lambda: session.add_public_vector(
            payment_ct,
            tens,
        ),
        # HE API call: OpenFHECreditSession.multiply_public_scalar(diff_ct, 0.5)
        "multiply_public_scalar": lambda: session.multiply_public_scalar(
            difference_ct,
            0.5,
        ),
        # HE API call: OpenFHECreditSession.multiply_public_vector(diff_ct, weights)
        "multiply_public_vector": lambda: session.multiply_public_vector(
            difference_ct,
            weights,
        ),
        # HE API call: OpenFHECreditSession.square(difference_ct)
        "square": lambda: session.square(difference_ct),
        # HE API call: OpenFHECreditSession.sum(difference_ct)
        "sum": lambda: session.sum(difference_ct),
        # HE API call: OpenFHECreditSession.mean(difference_ct)
        "mean": lambda: session.mean(difference_ct),
        # HE API call: OpenFHECreditSession.variance_components(difference_ct)
        "variance_components": lambda: session.variance_components(
            difference_ct
        ),
        # HE API call: OpenFHECreditSession.variance(difference_ct)
        "variance": lambda: session.variance(difference_ct),
        # HE API call: OpenFHECreditSession.covariance_components(parents)
        "covariance_components": lambda: session.covariance_components(
            installment_ct,
            payment_ct,
        ),
        # HE API call: OpenFHECreditSession.correlation_components(parents)
        "correlation_components": lambda: session.correlation_components(
            installment_ct,
            payment_ct,
        ),
        # HE API call: OpenFHECreditSession.weighted_sum(diff_ct, weights)
        "weighted_sum": lambda: session.weighted_sum(
            difference_ct,
            weights,
        ),
        # HE API call: OpenFHECreditSession.risk_score(diff_ct, weights, bias)
        "risk_score": lambda: session.risk_score(
            difference_ct,
            weights,
            1.5,
        ),
    }


def _write_report(
    root: Path,
    *,
    group: PreparedPaymentGroup,
    repetitions: int,
    setup_seconds: float,
    encrypt_seconds: float,
    rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# Direct OpenFHE-Python API latency",
        "",
        "Every row calls the named `OpenFHECreditSession` method. There is "
        "no HEIR compiler, generated C++, CMake, gateway, or HTTP service.",
        "",
        f"- Prepared group: `{group.applicant_id}`",
        f"- Real values: `{len(group.installment)}`",
        f"- CKKS slots reserved: `{group.slot_count}`",
        f"- Repetitions: `{repetitions}`",
        f"- One-time context and key setup: `{setup_seconds:.9f}` seconds",
        f"- Two parent-column encryptions: `{encrypt_seconds:.9f}` seconds",
        "",
        "| Function | Median call latency (s) | Max absolute error | "
        "Max relative error | Status |",
        "|---|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            f"| `{row['function']}` | "
            f"{float(row['median_latency_seconds']):.9f} | "
            f"{float(row['max_abs_error']):.12g} | "
            f"{float(row['max_relative_error']):.12g} | "
            f"{row['status']} |"
        )
    lines.extend(
        [
            "",
            "Method latency excludes setup, encryption, and final audit "
            "decryption. Aggregate methods consume the already-encrypted "
            "`PAYMENT_DIFF` column.",
        ]
    )
    (root / "REPORT.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def run_benchmark(
    *,
    prepared_group: Path,
    output_dir: Path,
    repetitions: int,
    multiplicative_depth: int,
    ring_dimension: int,
    absolute_tolerance: float,
    relative_tolerance: float,
    overwrite: bool,
    _openfhe_module: Any | None = None,
) -> dict[str, Any]:
    """Execute the direct API benchmark and write its three small artifacts."""
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

    group = load_prepared_group(prepared_group.resolve())
    # HE API call: OpenFHECreditSession(...) creates context and keys.
    session, setup_seconds = _timed(
        OpenFHECreditSession,
        slot_count=group.slot_count,
        multiplicative_depth=multiplicative_depth,
        ring_dimension=ring_dimension,
        _openfhe_module=_openfhe_module,
    )
    # HE API call: OpenFHECreditSession.encrypt(AMT_INSTALMENT)
    installment_ct, installment_encrypt = _timed(
        session.encrypt,
        group.installment,
    )
    # HE API call: OpenFHECreditSession.encrypt(AMT_PAYMENT)
    payment_ct, payment_encrypt = _timed(
        session.encrypt,
        group.payment,
    )
    # HE API call: OpenFHECreditSession.subtract(parent ciphertexts)
    difference_ct = session.subtract(installment_ct, payment_ct)
    expected = _expected_values(group)
    calls = _operation_calls(
        session,
        group,
        installment_ct,
        payment_ct,
        difference_ct,
    )

    rows: list[dict[str, Any]] = []
    for name, call in calls.items():
        latencies: list[float] = []
        result = None
        for _ in range(repetitions):
            result, elapsed = _timed(call)
            latencies.append(elapsed)
        observed = _decrypt_result(session, result)
        errors = _error_values(observed, expected[name])
        max_absolute = max(error[0] for error in errors)
        max_relative = max(error[1] for error in errors)
        passed = (
            max_absolute <= absolute_tolerance
            or max_relative <= relative_tolerance
        )
        rows.append(
            {
                "function": name,
                "median_latency_seconds": median(latencies),
                "max_abs_error": max_absolute,
                "max_relative_error": max_relative,
                "status": "PASS" if passed else "FAIL",
            }
        )

    with (root / "results.csv").open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    summary = {
        "status": (
            "PASS"
            if all(row["status"] == "PASS" for row in rows)
            else "FAIL"
        ),
        "backend": "official OpenFHE Python",
        "applicant_id": group.applicant_id,
        "real_values": len(group.installment),
        "slot_count": group.slot_count,
        "repetitions": repetitions,
        "setup_seconds": setup_seconds,
        "parent_encrypt_seconds": installment_encrypt + payment_encrypt,
        "functions": rows,
    }
    (root / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_report(
        root,
        group=group,
        repetitions=repetitions,
        setup_seconds=setup_seconds,
        encrypt_seconds=installment_encrypt + payment_encrypt,
        rows=rows,
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared-group", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--multiplicative-depth", type=int, default=4)
    parser.add_argument("--ring-dimension", type=int, default=0)
    parser.add_argument("--absolute-tolerance", type=float, default=1e-6)
    parser.add_argument("--relative-tolerance", type=float, default=1e-6)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    summary = run_benchmark(
        prepared_group=args.prepared_group,
        output_dir=args.output_dir,
        repetitions=args.repetitions,
        multiplicative_depth=args.multiplicative_depth,
        ring_dimension=args.ring_dimension,
        absolute_tolerance=args.absolute_tolerance,
        relative_tolerance=args.relative_tolerance,
        overwrite=args.overwrite,
    )
    print(json.dumps(summary, indent=2))
    if summary["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
