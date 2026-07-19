"""Registry and MLIR builders for the complete reusable arithmetic layer."""

from __future__ import annotations

from collections.abc import Callable

from code.heir.kernels.contracts import KernelContract
from code.heir.kernels.difference_moments import (
    CONTRACT as DIFFERENCE_MOMENTS_CONTRACT,
    difference_moments_mlir,
)
from code.heir.kernels.dot_product import CONTRACT as DOT_PRODUCT_CONTRACT, dot_product_mlir
from code.heir.kernels.linear_score import CONTRACT as LINEAR_SCORE_CONTRACT, linear_score_mlir
from code.heir.kernels.moments import CONTRACT as MOMENTS_CONTRACT, moments_mlir
from code.heir.kernels.polynomial_score import (
    CONTRACT as POLYNOMIAL_SCORE_CONTRACT,
    polynomial_score_mlir,
)


CONTRACTS: tuple[KernelContract, ...] = (
    DOT_PRODUCT_CONTRACT,
    MOMENTS_CONTRACT,
    DIFFERENCE_MOMENTS_CONTRACT,
    LINEAR_SCORE_CONTRACT,
    POLYNOMIAL_SCORE_CONTRACT,
)


def kernel_contracts() -> tuple[KernelContract, ...]:
    return CONTRACTS


def build_all_mlir(vector_size: int, polynomial_degree: int) -> dict[str, str]:
    """Return reviewable MLIR for every non-rule reusable kernel."""
    vector_builders: tuple[tuple[str, Callable[[int], str]], ...] = (
        (DOT_PRODUCT_CONTRACT.name, dot_product_mlir),
        (MOMENTS_CONTRACT.name, moments_mlir),
        (DIFFERENCE_MOMENTS_CONTRACT.name, difference_moments_mlir),
        (LINEAR_SCORE_CONTRACT.name, linear_score_mlir),
    )
    result = {name: builder(vector_size) for name, builder in vector_builders}
    result[POLYNOMIAL_SCORE_CONTRACT.name] = polynomial_score_mlir(polynomial_degree)
    return result
