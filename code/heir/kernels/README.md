# Reusable HEIR arithmetic layer

This folder contains fixed-shape arithmetic only. It deliberately has no Home
Credit table handling, rule engine, threshold comparison, tree traversal, or
LightGBM code.

| ID | Builder | Inputs | Encrypted result |
|---|---|---|---|
| K01 | `dot_product.py` | encrypted left and right vectors | dot product |
| K02 | `moments.py` | encrypted values and mask | count, sum, sum of squares |
| K03 | `difference_moments.py` | encrypted left, right, and mask | difference count, sum, sum of squares |
| S01 | `linear_score.py` | encrypted features; plaintext weights and bias | linear score |
| S02 | `polynomial_score.py` | encrypted scalar; plaintext coefficients | Horner polynomial result |

K01-K03 are source-derived reusable kernels. S01-S02 are special non-source
experiments and must retain that label in benchmark reports.

Every module provides two deliberately separate functions:

- `*_mlir(...)` emits fixed-size HEIR input MLIR.
- `*_reference(...)` computes the plaintext oracle used to check decrypted
  benchmark output.

`registry.py` is the only list of active reusable kernels. S03 LightGBM is not
registered and has no implementation.
