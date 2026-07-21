"""Single-output, non-bootstrap CKKS statistics kernels for a public count.

Each branch has one output deliberately. The installed HEIR/OpenFHE path can
reuse a ciphertext buffer while lowering a multiple-return function; returning
sum, mean and variance from separate calls makes the artifact boundary explicit
and prevents one branch from overwriting another.
"""

from __future__ import annotations

from collections.abc import Sequence

from code.heir.kernels.contracts import KernelContract


CONTRACT = KernelContract(
    kernel_id="K05",
    name="fixed_count_statistics_branches",
    entry_function="fixed_count_sum / fixed_count_mean / fixed_count_variance",
    lane="feature-derived review",
    operation="branch one encrypted source into sum, mean and sample variance",
    inputs=("values: encrypted vector", "public valid_count embedded in compiled circuit"),
    outputs=("one encrypted scalar per branch",),
    multiplicative_depth="1 after the input feature (variance branch)",
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


def _validate(vector_size: int, valid_count: int) -> None:
    if vector_size < 2:
        raise ValueError("vector size must be at least two")
    if not 2 <= valid_count <= vector_size:
        raise ValueError("valid_count must be in [2, vector_size]")


def _reduction(vector_size: int, valid_count: int, *, include_squares: bool) -> str:
    if include_squares:
        return f"""  %zero_index = arith.constant 0 : index
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
  }}"""
    return f"""  %zero_index = arith.constant 0 : index
  %first_value = tensor.extract %values[%zero_index] : tensor<{vector_size}xf64>
  %sum_result = affine.for %i = 1 to {valid_count}
      iter_args(%sum = %first_value) -> (f64) {{
    %value = tensor.extract %values[%i] : tensor<{vector_size}xf64>
    %next_sum = arith.addf %sum, %value : f64
    affine.yield %next_sum : f64
  }}"""


def fixed_count_sum_mlir(vector_size: int, valid_count: int) -> str:
    """Return an encrypted sum for the first public ``valid_count`` lanes."""
    _validate(vector_size, valid_count)
    return f"""func.func @fixed_count_sum(
    %values: tensor<{vector_size}xf64> {{secret.secret}}
) -> f64 {{
{_reduction(vector_size, valid_count, include_squares=False)}
  return %sum_result : f64
}}
"""


def fixed_count_mean_mlir(vector_size: int, valid_count: int) -> str:
    """Return encrypted mean for a public fixed count without reciprocal FHE."""
    _validate(vector_size, valid_count)
    inverse_count = 1.0 / valid_count
    return f"""func.func @fixed_count_mean(
    %values: tensor<{vector_size}xf64> {{secret.secret}}
) -> f64 {{
{_reduction(vector_size, valid_count, include_squares=False)}
  %inverse_count = arith.constant {inverse_count:.17g} : f64
  %mean = arith.mulf %sum_result, %inverse_count : f64
  return %mean : f64
}}
"""


def fixed_count_variance_mlir(vector_size: int, valid_count: int) -> str:
    """Return encrypted sample variance for a public fixed count."""
    _validate(vector_size, valid_count)
    inverse_count = 1.0 / valid_count
    inverse_denom = 1.0 / (valid_count - 1)
    return f"""func.func @fixed_count_variance(
    %values: tensor<{vector_size}xf64> {{secret.secret}}
) -> f64 {{
{_reduction(vector_size, valid_count, include_squares=True)}
  %inverse_count = arith.constant {inverse_count:.17g} : f64
  %inverse_denom = arith.constant {inverse_denom:.17g} : f64
  %mean = arith.mulf %sum_result, %inverse_count : f64
  %sum_times_mean = arith.mulf %sum_result, %mean : f64
  %variance_numerator = arith.subf %squares_result, %sum_times_mean : f64
  %sample_variance = arith.mulf %variance_numerator, %inverse_denom : f64
  return %sample_variance : f64
}}
"""
