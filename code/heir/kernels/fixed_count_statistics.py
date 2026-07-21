"""A small non-bootstrap CKKS statistics kernel for a public fixed count.

This is intentionally *not* the general grouped-aggregation solution.  It is
the lightest honest proof that one encrypted feature ciphertext can produce
encrypted sum, mean and sample variance when the group count is public and
fixed by the packing contract.
"""

from __future__ import annotations

from collections.abc import Sequence

from code.heir.kernels.contracts import KernelContract


CONTRACT = KernelContract(
    kernel_id="K05",
    name="fixed_count_statistics",
    entry_function="fixed_count_statistics",
    lane="feature-derived review",
    operation="sum, mean and sample variance for a public fixed packed count",
    inputs=("values: encrypted vector", "public valid_count embedded in compiled circuit"),
    outputs=(
        "sum: encrypted scalar",
        "mean: encrypted scalar",
        "sample_variance: encrypted scalar",
    ),
    multiplicative_depth="1 after the input feature (square and sum*mean)",
    expected_evaluation_keys=("relinearization",),
    ckks_parameters_status="bounded fixed-count review kernel",
)


def fixed_count_statistics_reference(values: Sequence[float]) -> tuple[float, float, float]:
    """Plaintext oracle for the same fixed, non-empty group."""
    if len(values) < 2:
        raise ValueError("sample variance requires at least two values")
    total = sum(float(value) for value in values)
    count = float(len(values))
    mean = total / count
    variance = sum((float(value) - mean) ** 2 for value in values) / (count - 1.0)
    return total, mean, variance


def fixed_count_statistics_mlir(vector_size: int, valid_count: int) -> str:
    """Return sum, mean and sample variance with an explicit public count.

    The first ``valid_count`` lanes are the group. Remaining packed lanes are
    ignored. A production grouped workload with private/variable group sizes
    must instead keep an encrypted count and use the deeper finalizer.
    """
    if vector_size < 2:
        raise ValueError("vector size must be at least two")
    if not 2 <= valid_count <= vector_size:
        raise ValueError("valid_count must be in [2, vector_size]")
    inverse_count = 1.0 / valid_count
    inverse_denom = 1.0 / (valid_count - 1)
    return f"""func.func @fixed_count_statistics(
    %values: tensor<{vector_size}xf64> {{secret.secret}}
) -> (f64, f64, f64) {{
  // valid_count={valid_count} is a public packing contract, not encrypted data.
  %zero_index = arith.constant 0 : index
  %first_value = tensor.extract %values[%zero_index] : tensor<{vector_size}xf64>
  %first_square = arith.mulf %first_value, %first_value : f64
  %sum_result, %squares_result = affine.for %i = 1 to {valid_count}
      iter_args(%sum = %first_value, %squares = %first_square)
      -> (f64, f64) {{
    %value = tensor.extract %values[%i] : tensor<{vector_size}xf64>
    %square = arith.mulf %value, %value : f64
    %next_sum = arith.addf %sum, %value : f64
    %next_squares = arith.addf %squares, %square : f64
    affine.yield %next_sum, %next_squares : f64, f64
  }}
  %inverse_count = arith.constant {inverse_count:.17g} : f64
  %inverse_denom = arith.constant {inverse_denom:.17g} : f64
  %mean = arith.mulf %sum_result, %inverse_count : f64
  %sum_times_mean = arith.mulf %sum_result, %mean : f64
  %variance_numerator = arith.subf %squares_result, %sum_times_mean : f64
  %sample_variance = arith.mulf %variance_numerator, %inverse_denom : f64
  return %sum_result, %mean, %sample_variance : f64, f64, f64
}}
"""
