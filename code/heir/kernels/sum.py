"""Generic encrypted sum used after a feature ciphertext is produced."""

from __future__ import annotations


def encrypted_sum_mlir(vector_size: int) -> str:
    if vector_size <= 0:
        raise ValueError("vector_size must be positive")
    tensor = f"tensor<{vector_size}xf64>"
    return f"""func.func @encrypted_sum(
    %values: {tensor} {{secret.secret}}
) -> f64 {{
  %zero = arith.constant 0.0 : f64
  %sum = affine.for %i = 0 to {vector_size} iter_args(%total = %zero) -> (f64) {{
    %value = tensor.extract %values[%i] : {tensor}
    %next = arith.addf %total, %value : f64
    affine.yield %next : f64
  }}
  return %sum : f64
}}
"""
