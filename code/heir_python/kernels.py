"""Self-contained HEIR MLIR sources for fixed-count CKKS aggregates."""

from __future__ import annotations


def _validate(width: int, valid_count: int) -> None:
    if width < 2:
        raise ValueError("width must be at least two")
    if not 2 <= valid_count <= width:
        raise ValueError("valid_count must be in [2, width]")


def _sum_reduction(width: int, valid_count: int) -> str:
    tensor = f"tensor<{width}xf64>"
    lines: list[str] = []
    current: list[str] = []
    for index in range(valid_count):
        lines.append(f"  %index_{index} = arith.constant {index} : index")
        value = f"%value_{index}"
        lines.append(
            f"  {value} = tensor.extract %values[%index_{index}] : {tensor}"
        )
        current.append(value)
    operation = 0
    while len(current) > 1:
        next_level: list[str] = []
        for index in range(0, len(current), 2):
            if index + 1 == len(current):
                next_level.append(current[index])
                continue
            name = (
                "%sum_result"
                if len(current) == 2
                else f"%sum_tree_{operation}"
            )
            lines.append(
                f"  {name} = arith.addf {current[index]}, "
                f"{current[index + 1]} : f64"
            )
            next_level.append(name)
            operation += 1
        current = next_level
    return "\n".join(lines)


def _sum_and_squares_reduction(width: int, valid_count: int) -> str:
    tensor = f"tensor<{width}xf64>"
    lines = [
        "  %zero_index = arith.constant 0 : index",
        f"  %first_value = tensor.extract %values[%zero_index] : {tensor}",
        "  %first_square = arith.mulf %first_value, %first_value : f64",
        f"  %sum_result, %squares_result = affine.for %i = 1 to {valid_count}",
        "      iter_args(%sum = %first_value, %squares = %first_square)",
        "      -> (f64, f64) {",
        f"    %value = tensor.extract %values[%i] : {tensor}",
        "    %square = arith.mulf %value, %value : f64",
        "    %next_sum = arith.addf %sum, %value : f64",
        "    %next_squares = arith.addf %squares, %square : f64",
        "    affine.yield %next_sum, %next_squares : f64, f64",
        "  }",
    ]
    return "\n".join(lines)


def fixed_count_sum_mlir(width: int, valid_count: int) -> str:
    """Return a single encrypted SUM for the public valid lane count."""
    _validate(width, valid_count)
    return f"""func.func @fixed_count_sum(
    %values: tensor<{width}xf64> {{secret.secret}}
) -> f64 {{
{_sum_reduction(width, valid_count)}
  return %sum_result : f64
}}
"""


def fixed_count_mean_mlir(width: int, valid_count: int) -> str:
    """Return encrypted MEAN using a public compile-time reciprocal."""
    _validate(width, valid_count)
    inverse_count = 1.0 / valid_count
    return f"""func.func @fixed_count_mean(
    %values: tensor<{width}xf64> {{secret.secret}}
) -> f64 {{
{_sum_reduction(width, valid_count)}
  %inverse_count = arith.constant {inverse_count:.17g} : f64
  %mean = arith.mulf %sum_result, %inverse_count : f64
  return %mean : f64
}}
"""


def fixed_count_variance_mlir(width: int, valid_count: int) -> str:
    """Return encrypted sample variance for a public compile-time count."""
    _validate(width, valid_count)
    inverse_count = 1.0 / valid_count
    inverse_denom = 1.0 / (valid_count - 1)
    return f"""func.func @fixed_count_variance(
    %values: tensor<{width}xf64> {{secret.secret}}
) -> f64 {{
{_sum_and_squares_reduction(width, valid_count)}
  %inverse_count = arith.constant {inverse_count:.17g} : f64
  %inverse_denom = arith.constant {inverse_denom:.17g} : f64
  %mean = arith.mulf %sum_result, %inverse_count : f64
  %sum_times_mean = arith.mulf %sum_result, %mean : f64
  %variance_numerator = arith.subf %squares_result, %sum_times_mean : f64
  %sample_variance = arith.mulf %variance_numerator, %inverse_denom : f64
  return %sample_variance : f64
}}
"""
