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


def ratio_newton_mlir(
    vector_size: int,
    *,
    entry_function: str = "encrypted_ratio_newton",
    denominator_scale: float = 1000.0,
) -> str:
    """Emit generic encrypted ``numerator / denominator`` CKKS MLIR.

    The client supplies raw numerator/denominator columns and a validity mask;
    it does not calculate the ratio. ``denominator_scale`` and the normalized
    denominator interval are public representation/range contracts.
    """
    if vector_size <= 0 or denominator_scale <= 0:
        raise ValueError("vector size and denominator scale must be positive")
    tensor = f"tensor<{vector_size}xf64>"
    inverse_scale = 1.0 / denominator_scale
    return f'''func.func @{entry_function}(
    %numerator: {tensor} {{secret.secret}},
    %denominator: {tensor} {{secret.secret}},
    %valid: {tensor} {{secret.secret}}
) -> {tensor} {{
  %zero = arith.constant dense<0.0> : {tensor}
  %inv_scale = arith.constant {inverse_scale:.17g} : f64
  %a = arith.constant 2.8235294117647058 : f64
  %b = arith.constant 1.8823529411764706 : f64
  %two = arith.constant 2.0 : f64
  %result = affine.for %i = 0 to {vector_size} iter_args(%out = %zero) -> ({tensor}) {{
    %n = tensor.extract %numerator[%i] : {tensor}
    %d_raw = tensor.extract %denominator[%i] : {tensor}
    %m = tensor.extract %valid[%i] : {tensor}
    %d = arith.mulf %d_raw, %inv_scale : f64
    // Newton reciprocal is calibrated for normalized d in [0.5, 1.0].
    %seed_product = arith.mulf %b, %d : f64
    %x0 = arith.subf %a, %seed_product : f64
    %step0_product = arith.mulf %d, %x0 : f64
    %step0_error = arith.subf %two, %step0_product : f64
    %x1 = arith.mulf %x0, %step0_error : f64
    %step1_product = arith.mulf %d, %x1 : f64
    %step1_error = arith.subf %two, %step1_product : f64
    %inverse_normalized = arith.mulf %x1, %step1_error : f64
    %unscaled = arith.mulf %n, %inverse_normalized : f64
    %ratio = arith.mulf %unscaled, %inv_scale : f64
    %masked_ratio = arith.mulf %ratio, %m : f64
    %next = tensor.insert %masked_ratio into %out[%i] : {tensor}
    affine.yield %next : {tensor}
  }}
  return %result : {tensor}
}}
'''
