"""Generic column expressions and their HEIR MLIR/source-level contracts."""

from __future__ import annotations

import math
from collections.abc import Sequence

from code.heir.operations.contracts import require_implemented


def prepare_nullable_column(values: Sequence[float | int | None]) -> tuple[list[float], list[float]]:
    """Encode nulls as zero plus a validity mask without computing a feature."""
    packed: list[float] = []
    validity: list[float] = []
    for value in values:
        if value is None:
            packed.append(0.0)
            validity.append(0.0)
            continue
        numeric = float(value)
        if not math.isfinite(numeric):
            packed.append(0.0)
            validity.append(0.0)
        else:
            packed.append(numeric)
            validity.append(1.0)
    return packed, validity


def binary_reference(
    left: Sequence[float], right: Sequence[float], operation: str
) -> list[float]:
    """Plaintext audit oracle only; production calculation occurs after encryption."""
    require_implemented(operation)
    if len(left) != len(right):
        raise ValueError("binary encrypted columns must have equal length")
    if operation == "add":
        return [float(a) + float(b) for a, b in zip(left, right)]
    if operation == "subtract":
        return [float(a) - float(b) for a, b in zip(left, right)]
    if operation == "multiply":
        return [float(a) * float(b) for a, b in zip(left, right)]
    raise ValueError(f"{operation} is not a binary column operation")


def binary_mlir(vector_size: int, operation: str) -> str:
    """Emit a function-agnostic encrypted element-wise operation."""
    if vector_size <= 0:
        raise ValueError("vector_size must be positive")
    require_implemented(operation)
    opcode = {"add": "addf", "subtract": "subf", "multiply": "mulf"}.get(operation)
    if opcode is None:
        raise ValueError(f"{operation} is not a binary column operation")
    name = f"encrypted_{operation}"
    tensor = f"tensor<{vector_size}xf64>"
    return f"""func.func @{name}(
    %left: {tensor} {{secret.secret}},
    %right: {tensor} {{secret.secret}}
) -> {tensor} {{
  %result = arith.{opcode} %left, %right : {tensor}
  return %result : {tensor}
}}
"""


def masked_binary_mlir(vector_size: int, operation: str) -> str:
    """Emit a binary column operation with an encrypted row/group mask."""
    if vector_size <= 0:
        raise ValueError("vector_size must be positive")
    require_implemented(operation)
    opcode = {"add": "addf", "subtract": "subf", "multiply": "mulf"}.get(operation)
    if opcode is None:
        raise ValueError(f"{operation} is not a binary column operation")
    name = f"encrypted_masked_{operation}"
    tensor = f"tensor<{vector_size}xf64>"
    return f"""func.func @{name}(
    %left: {tensor} {{secret.secret}},
    %right: {tensor} {{secret.secret}},
    %mask: {tensor} {{secret.secret}}
) -> {tensor} {{
  %raw = arith.{opcode} %left, %right : {tensor}
  %result = arith.mulf %raw, %mask : {tensor}
  return %result : {tensor}
}}
"""
