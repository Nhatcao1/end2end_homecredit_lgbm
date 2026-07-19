"""Generate the fixed-size arithmetic kernel lowered by HEIR to CKKS/OpenFHE."""

from __future__ import annotations


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
