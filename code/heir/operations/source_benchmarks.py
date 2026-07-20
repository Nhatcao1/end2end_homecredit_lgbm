"""Source-faithful, small benchmarks built on generic encrypted columns.

This module intentionally does not make Home Credit features during input
preparation.  It reads raw source columns, packs nulls, and identifies the
generic encrypted operation which must run after encryption.
"""

from __future__ import annotations

import csv
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from code.heir.operations.columns import binary_reference, prepare_nullable_column
from code.heir.operations.contracts import operation_contract


@dataclass(frozen=True)
class SourceBenchmarkSpec:
    benchmark_id: str
    source_function: str
    source_lines: str
    source_expression: str
    input_file: str
    left_column: str
    right_column: str
    operation: str
    status: str
    notes: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


SPECS: tuple[SourceBenchmarkSpec, ...] = (
    SourceBenchmarkSpec(
        benchmark_id="installments_payment_diff",
        source_function="installments_payments()",
        source_lines="203-204",
        source_expression="PAYMENT_DIFF = AMT_INSTALMENT - AMT_PAYMENT",
        input_file="installments_payments.csv",
        left_column="AMT_INSTALMENT",
        right_column="AMT_PAYMENT",
        operation="subtract",
        status="executable_exact_ckks",
        notes="The first real encrypted evaluation benchmark. Null validity is an encrypted mask for later reduction.",
    ),
    SourceBenchmarkSpec(
        benchmark_id="application_days_employed_perc",
        source_function="application_train_test()",
        source_lines="61, 64",
        source_expression="DAYS_EMPLOYED_PERC = DAYS_EMPLOYED / DAYS_BIRTH",
        input_file="application_train.csv",
        left_column="DAYS_EMPLOYED",
        right_column="DAYS_BIRTH",
        operation="divide",
        status="deferred_reciprocal_polynomial",
        notes="365243 is packed as null client-side; division itself must be encrypted via a bounded reciprocal approximation.",
    ),
    SourceBenchmarkSpec(
        benchmark_id="installments_dpd_clip",
        source_function="installments_payments()",
        source_lines="206, 208",
        source_expression="DPD = max(DAYS_ENTRY_PAYMENT - DAYS_INSTALMENT, 0)",
        input_file="installments_payments.csv",
        left_column="DAYS_ENTRY_PAYMENT",
        right_column="DAYS_INSTALMENT",
        operation="threshold",
        status="deferred_comparison_route",
        notes="Exact subtraction is CKKS; clipping needs a CKKS approximation or a separately measured CKKS-to-FHEW comparison route.",
    ),
)


def benchmark_spec(benchmark_id: str) -> SourceBenchmarkSpec:
    for spec in SPECS:
        if spec.benchmark_id == benchmark_id:
            return spec
    raise ValueError(f"unknown source benchmark: {benchmark_id}")


def _number(value: str | None, *, null_sentinel: float | None = None) -> float | None:
    if value is None or not value.strip():
        return None
    try:
        numeric = float(value)
    except ValueError:
        return None
    if not math.isfinite(numeric) or (null_sentinel is not None and numeric == null_sentinel):
        return None
    return numeric


def prepare_binary_source_benchmark(
    spec: SourceBenchmarkSpec, data_dir: Path, row_limit: int = 0
) -> dict[str, Any]:
    """Read raw columns and pack them, without evaluating the source feature."""
    path = data_dir / spec.input_file
    if not path.is_file():
        raise FileNotFoundError(f"missing source input: {path}")
    left_raw: list[float | None] = []
    right_raw: list[float | None] = []
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        missing = {spec.left_column, spec.right_column}.difference(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path.name} is missing columns: {sorted(missing)}")
        for index, row in enumerate(reader):
            if row_limit > 0 and index >= row_limit:
                break
            # This is the source notebook's DAYS_EMPLOYED sentinel replacement,
            # not a feature calculation.
            sentinel = 365243.0 if spec.left_column == "DAYS_EMPLOYED" else None
            left_raw.append(_number(row.get(spec.left_column), null_sentinel=sentinel))
            right_raw.append(_number(row.get(spec.right_column)))
    if not left_raw:
        raise ValueError("source input produced no rows")
    left, left_valid = prepare_nullable_column(left_raw)
    right, right_valid = prepare_nullable_column(right_raw)
    valid_count = sum(1 for a, b in zip(left_valid, right_valid) if a == b == 1.0)
    result: dict[str, Any] = {
        "spec": spec.to_dict(),
        "left": left,
        "right": right,
        "left_validity": left_valid,
        "right_validity": right_valid,
        "row_count": len(left),
        "valid_pair_count": valid_count,
        "operation_contract": operation_contract(spec.operation).to_dict(),
    }
    return result


def plaintext_audit_reference(prepared: dict[str, Any]) -> list[float]:
    """Run the source expression solely as the timed plaintext baseline/audit.

    This deliberately is not called by ``prepare_binary_source_benchmark``.
    The prepare phase must never derive the feature before encryption.
    """
    spec = prepared["spec"]
    return binary_reference(prepared["left"], prepared["right"], spec["operation"])
