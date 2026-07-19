"""Special non-source low-degree polynomial evaluated with Horner's method."""

from __future__ import annotations

from collections.abc import Sequence

from code.heir.kernels.contracts import KernelContract


CONTRACT = KernelContract(
    kernel_id="S02",
    name="polynomial_score",
    entry_function="polynomial_score",
    lane="special-non-source",
    operation="c[0] + c[1]x + ... + c[d]x^d using Horner evaluation",
    inputs=("value: encrypted scalar", "coefficients: plaintext vector"),
    outputs=("transformed_score: encrypted scalar",),
    multiplicative_depth="polynomial degree",
    expected_evaluation_keys=("relinearization",),
)


def polynomial_score_reference(value: float, coefficients: Sequence[float]) -> float:
    """Evaluate ascending-order coefficients using the same Horner schedule."""
    if len(coefficients) < 2:
        raise ValueError("at least two coefficients are required")
    result = float(coefficients[-1])
    for coefficient in reversed(coefficients[:-1]):
        result = result * float(value) + float(coefficient)
    return result


def polynomial_score_mlir(degree: int) -> str:
    if degree <= 0:
        raise ValueError("degree must be positive")
    coefficient_count = degree + 1
    lines = [
        "func.func @polynomial_score(",
        "    %value: f64 {secret.secret},",
        f"    %coefficients: tensor<{coefficient_count}xf64>",
        ") -> f64 {",
        f"  %index_{degree} = arith.constant {degree} : index",
        (
            f"  %coefficient_{degree} = tensor.extract "
            f"%coefficients[%index_{degree}] : tensor<{coefficient_count}xf64>"
        ),
    ]
    accumulator = f"%coefficient_{degree}"
    for index in range(degree - 1, -1, -1):
        lines.extend(
            [
                f"  %index_{index} = arith.constant {index} : index",
                (
                    f"  %coefficient_{index} = tensor.extract "
                    f"%coefficients[%index_{index}] : tensor<{coefficient_count}xf64>"
                ),
                f"  %product_{index} = arith.mulf {accumulator}, %value : f64",
                (
                    f"  %accumulator_{index} = arith.addf %product_{index}, "
                    f"%coefficient_{index} : f64"
                ),
            ]
        )
        accumulator = f"%accumulator_{index}"
    lines.extend([f"  return {accumulator} : f64", "}", ""])
    return "\n".join(lines)
