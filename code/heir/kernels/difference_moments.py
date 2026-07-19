"""Reusable sufficient statistics over an encrypted element-wise difference."""

from __future__ import annotations

from collections.abc import Sequence

from code.heir.kernels.contracts import KernelContract


CONTRACT = KernelContract(
    kernel_id="K03",
    name="difference_moments",
    entry_function="difference_moments",
    lane="source-derived",
    operation="moments(left[i] - right[i], mask[i])",
    inputs=(
        "left: encrypted vector",
        "right: encrypted vector",
        "mask: encrypted vector",
    ),
    outputs=(
        "count: encrypted scalar",
        "difference_sum: encrypted scalar",
        "difference_sum_squares: encrypted scalar",
    ),
    multiplicative_depth="2",
    expected_evaluation_keys=("relinearization", "rotations for packed reductions"),
)


def difference_moments_reference(
    left: Sequence[float], right: Sequence[float], mask: Sequence[float]
) -> tuple[float, float, float]:
    """Plaintext oracle for K03 without creating a business-specific feature."""
    if len(left) != len(right) or len(left) != len(mask):
        raise ValueError("left, right, and mask must have equal length")
    if not left:
        raise ValueError("vectors must not be empty")
    count = total = sum_squares = 0.0
    for raw_left, raw_right, raw_mask in zip(left, right, mask):
        difference = float(raw_left) - float(raw_right)
        weight = float(raw_mask)
        count += weight
        total += weight * difference
        sum_squares += weight * difference * difference
    return count, total, sum_squares


def difference_moments_mlir(vector_size: int) -> str:
    if vector_size <= 0:
        raise ValueError("vector_size must be positive")
    return f"""func.func @difference_moments(
    %left: tensor<{vector_size}xf64> {{secret.secret}},
    %right: tensor<{vector_size}xf64> {{secret.secret}},
    %mask: tensor<{vector_size}xf64> {{secret.secret}}
) -> (f64, f64, f64) {{
  %zero = arith.constant 0.0 : f64
  %count_result, %sum_result, %square_result = affine.for %i = 0 to {vector_size}
      iter_args(%count = %zero, %sum = %zero, %sum_squares = %zero)
      -> (f64, f64, f64) {{
    %left_value = tensor.extract %left[%i] : tensor<{vector_size}xf64>
    %right_value = tensor.extract %right[%i] : tensor<{vector_size}xf64>
    %weight = tensor.extract %mask[%i] : tensor<{vector_size}xf64>
    %difference = arith.subf %left_value, %right_value : f64
    %weighted_difference = arith.mulf %weight, %difference : f64
    %difference_squared = arith.mulf %difference, %difference : f64
    %weighted_square = arith.mulf %weight, %difference_squared : f64
    %next_count = arith.addf %count, %weight : f64
    %next_sum = arith.addf %sum, %weighted_difference : f64
    %next_squares = arith.addf %sum_squares, %weighted_square : f64
    affine.yield %next_count, %next_sum, %next_squares : f64, f64, f64
  }}
  return %count_result, %sum_result, %square_result : f64, f64, f64
}}
"""
