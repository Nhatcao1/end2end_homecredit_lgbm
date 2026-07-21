"""One function-agnostic API for encrypted column expressions and reductions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from code.heir.kernels.fixed_count_statistics import (
    fixed_count_mean_mlir,
    fixed_count_sum_mlir,
    fixed_count_variance_mlir,
)
from code.heir.operations.columns import binary_mlir, ratio_newton_mlir


Aggregation = Literal["sum", "mean", "var", "max", "min"]


@dataclass(frozen=True)
class EncryptedOperationPlan:
    """A portable contract for a generic encrypted operation."""

    operation: str
    route: str
    status: str
    detail: str
    mlir: str | None = None


def encrypted_column(operation: str, vector_size: int) -> EncryptedOperationPlan:
    """Plan generic encrypted add/subtract/multiply or bounded ratio."""
    normalized = operation.strip().lower()
    if normalized in {"add", "subtract", "multiply"}:
        return EncryptedOperationPlan(normalized, "HEIR CKKS", "implemented", "element-wise ciphertext column operation", binary_mlir(vector_size, normalized))
    if normalized in {"divide", "ratio"}:
        return EncryptedOperationPlan("ratio", "HEIR CKKS reciprocal polynomial", "implemented_with_range_contract", "approximate encrypted division; requires positive bounded denominator and validity mask", ratio_newton_mlir(vector_size))
    raise ValueError(f"unsupported encrypted column operation: {operation}")


def encrypted_aggregation(
    operation: Aggregation, vector_size: int, public_valid_count: int
) -> EncryptedOperationPlan:
    """Plan one encrypted aggregation over a packed feature ciphertext."""
    normalized = operation.strip().lower()
    if normalized == "sum":
        return EncryptedOperationPlan("sum", "HEIR CKKS", "implemented", "one-output packed reduction", fixed_count_sum_mlir(vector_size, public_valid_count))
    if normalized == "mean":
        return EncryptedOperationPlan("mean", "HEIR CKKS", "implemented_with_public_count", "one-output reduction; public fixed group count only", fixed_count_mean_mlir(vector_size, public_valid_count))
    if normalized == "var":
        return EncryptedOperationPlan("var", "HEIR CKKS", "implemented_with_public_count", "one-output sample variance; public fixed group count only", fixed_count_variance_mlir(vector_size, public_valid_count))
    if normalized in {"max", "min"}:
        return EncryptedOperationPlan(normalized, "OpenFHE CKKS↔FHEW", "separate_session_required", "comparison/scheme-switch session with a public range contract; not emitted by HEIR", None)
    raise ValueError(f"unsupported encrypted aggregation: {operation}")
