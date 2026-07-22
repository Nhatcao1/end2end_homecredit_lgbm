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


def _balanced_reduction(vector_size: int, valid_count: int, *, include_squares: bool) -> str:
    """Emit a pairwise tree, avoiding a long running encrypted accumulator."""
    tensor = f"tensor<{vector_size}xf64>"
    lines: list[str] = []
    if include_squares:
        current: list[tuple[str, str]] = []
        for index in range(valid_count):
            lines.append(f"  %index_{index} = arith.constant {index} : index")
            value, square = f"%value_{index}", f"%square_{index}"
            lines.append(f"  {value} = tensor.extract %values[%index_{index}] : {tensor}")
            lines.append(f"  {square} = arith.mulf {value}, {value} : f64")
            current.append((value, square))
        operation = 0
        while len(current) > 1:
            next_level: list[tuple[str, str]] = []
            for index in range(0, len(current), 2):
                if index + 1 == len(current):
                    next_level.append(current[index])
                    continue
                last_pair = len(current) == 2
                sum_name = "%sum_result" if last_pair else f"%sum_tree_{operation}"
                square_name = "%squares_result" if last_pair else f"%squares_tree_{operation}"
                left, right = current[index], current[index + 1]
                lines.append(f"  {sum_name} = arith.addf {left[0]}, {right[0]} : f64")
                lines.append(f"  {square_name} = arith.addf {left[1]}, {right[1]} : f64")
                next_level.append((sum_name, square_name))
                operation += 1
            current = next_level
        return "\n".join(lines)
    current_values: list[str] = []
    for index in range(valid_count):
        lines.append(f"  %index_{index} = arith.constant {index} : index")
        value = f"%value_{index}"
        lines.append(f"  {value} = tensor.extract %values[%index_{index}] : {tensor}")
        current_values.append(value)
    operation = 0
    while len(current_values) > 1:
        next_level: list[str] = []
        for index in range(0, len(current_values), 2):
            if index + 1 == len(current_values):
                next_level.append(current_values[index])
                continue
            name = "%sum_result" if len(current_values) == 2 else f"%sum_tree_{operation}"
            lines.append(f"  {name} = arith.addf {current_values[index]}, {current_values[index + 1]} : f64")
            next_level.append(name)
            operation += 1
        current_values = next_level
    return "\n".join(lines)


def fixed_count_sum_mlir(vector_size: int, valid_count: int) -> str:
    """Return an encrypted sum for the first public ``valid_count`` lanes."""
    _validate(vector_size, valid_count)
    return f"""func.func @fixed_count_sum(
    %values: tensor<{vector_size}xf64> {{secret.secret}}
) -> f64 {{
{_balanced_reduction(vector_size, valid_count, include_squares=False)}
  return %sum_result : f64
}}
"""


def fixed_count_sum_squares_mlir(vector_size: int, valid_count: int) -> str:
    """Return encrypted packed ``sum(values ** 2)`` for a public lane count.

    The tensor multiplication is deliberately applied before extracting lanes.
    This uses the same packed CT×CT representation as the standalone primitive
    benchmark rather than emitting one scalar multiplication per lane.
    """
    _validate(vector_size, valid_count)
    tensor = f"tensor<{vector_size}xf64>"
    current: list[str] = []
    lines = [
        "func.func @fixed_count_sum_squares(",
        f"    %values: {tensor} {{secret.secret}}",
        ") -> f64 {",
        f"  %squares = arith.mulf %values, %values : {tensor}",
    ]
    for index in range(valid_count):
        index_name = f"%index_{index}"
        value_name = f"%square_{index}"
        lines.append(f"  {index_name} = arith.constant {index} : index")
        lines.append(f"  {value_name} = tensor.extract %squares[{index_name}] : {tensor}")
        current.append(value_name)
    operation = 0
    while len(current) > 1:
        next_level: list[str] = []
        for index in range(0, len(current), 2):
            if index + 1 == len(current):
                next_level.append(current[index])
                continue
            name = "%squares_result" if len(current) == 2 else f"%squares_tree_{operation}"
            lines.append(f"  {name} = arith.addf {current[index]}, {current[index + 1]} : f64")
            next_level.append(name)
            operation += 1
        current = next_level
    lines.extend(["  return %squares_result : f64", "}"])
    return "\n".join(lines) + "\n"


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
