"""Reusable encrypted/encrypted dot-product kernel."""

from __future__ import annotations

from collections.abc import Sequence

from code.heir.kernels.contracts import KernelContract


CONTRACT = KernelContract(
    kernel_id="K01",
    name="dot_product_ct_ct",
    entry_function="dot_product",
    lane="source-derived",
    operation="sum(left[i] * right[i])",
    inputs=("left: encrypted vector", "right: encrypted vector"),
    outputs=("dot_product: encrypted scalar",),
    multiplicative_depth="1",
    expected_evaluation_keys=("relinearization", "rotations for packed reduction"),
    generated_ckks_status="runner_available",
)


def dot_product_reference(left: Sequence[float], right: Sequence[float]) -> float:
    """Plaintext oracle used to validate a decrypted K01 result."""
    if len(left) != len(right):
        raise ValueError("left and right must have equal length")
    if not left:
        raise ValueError("vectors must not be empty")
    return sum(float(x) * float(y) for x, y in zip(left, right))


def dot_product_mlir(vector_size: int) -> str:
    if vector_size <= 0:
        raise ValueError("vector_size must be positive")
    return f"""func.func @dot_product(
    %arg0: tensor<{vector_size}xf64> {{secret.secret}},
    %arg1: tensor<{vector_size}xf64> {{secret.secret}}
) -> f64 {{
  %zero = arith.constant 0.0 : f64
  %result = affine.for %i = 0 to {vector_size}
      iter_args(%sum = %zero) -> (f64) {{
    %x = tensor.extract %arg0[%i] : tensor<{vector_size}xf64>
    %y = tensor.extract %arg1[%i] : tensor<{vector_size}xf64>
    %product = arith.mulf %x, %y : f64
    %next = arith.addf %sum, %product : f64
    affine.yield %next : f64
  }}
  return %result : f64
}}
"""
