"""Reusable masked sufficient-statistics kernel for CKKS benchmarks."""

from __future__ import annotations

from collections.abc import Sequence

from code.heir.kernels.contracts import KernelContract


CONTRACT = KernelContract(
    kernel_id="K02",
    name="moments",
    entry_function="moments",
    lane="source-derived",
    operation="sum(mask), sum(mask * value), sum(mask * value^2)",
    inputs=("values: encrypted vector", "mask: encrypted vector"),
    outputs=(
        "count: encrypted scalar",
        "sum: encrypted scalar",
        "sum_squares: encrypted scalar",
    ),
    multiplicative_depth="2",
    expected_evaluation_keys=("relinearization", "rotations for packed reductions"),
)


def moments_reference(
    values: Sequence[float], mask: Sequence[float]
) -> tuple[float, float, float]:
    """Return count, sum, and sum of squares for decrypted-result checks."""
    if len(values) != len(mask):
        raise ValueError("values and mask must have equal length")
    if not values:
        raise ValueError("vectors must not be empty")
    count = total = sum_squares = 0.0
    for raw_value, raw_mask in zip(values, mask):
        value = float(raw_value)
        weight = float(raw_mask)
        count += weight
        total += weight * value
        sum_squares += weight * value * value
    return count, total, sum_squares


def moments_mlir(vector_size: int) -> str:
    """Build fixed-shape MLIR; mean and variance remain trusted post-processing."""
    if vector_size <= 0:
        raise ValueError("vector_size must be positive")
    return f"""func.func @moments(
    %values: tensor<{vector_size}xf64> {{secret.secret}},
    %mask: tensor<{vector_size}xf64> {{secret.secret}}
) -> (f64, f64, f64) {{
  %zero = arith.constant 0.0 : f64
  %count_result, %sum_result, %square_result = affine.for %i = 0 to {vector_size}
      iter_args(%count = %zero, %sum = %zero, %sum_squares = %zero)
      -> (f64, f64, f64) {{
    %value = tensor.extract %values[%i] : tensor<{vector_size}xf64>
    %weight = tensor.extract %mask[%i] : tensor<{vector_size}xf64>
    %weighted_value = arith.mulf %weight, %value : f64
    %value_squared = arith.mulf %value, %value : f64
    %weighted_square = arith.mulf %weight, %value_squared : f64
    %next_count = arith.addf %count, %weight : f64
    %next_sum = arith.addf %sum, %weighted_value : f64
    %next_squares = arith.addf %sum_squares, %weighted_square : f64
    affine.yield %next_count, %next_sum, %next_squares : f64, f64, f64
  }}
  return %count_result, %sum_result, %square_result : f64, f64, f64
}}
"""
