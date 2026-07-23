# Python-facing HEIR operations

This project originally used HEIR's command-line compiler and an OpenFHE C++
harness. That is appropriate for reproducible benchmarks, but it is not the
API an application developer should start from.

For a Python user API, install the official frontend in a dedicated virtual
environment:

```bash
python3 -m venv .venv-heir-py
source .venv-heir-py/bin/activate
python3 -m pip install "heir_py[python,openfhe]"
```

Then run the smallest real encrypted operation:

```bash
python3 code/heir/examples/heir_py_ckks_sum_mean.py
```

The example uses the intended application-facing sequence:

```text
@compile(scheme="ckks")
    -> setup()
    -> encrypt_input(...)
    -> eval(encrypted inputs)
    -> decrypt_result(...)     # audit/client boundary only
```

## Capability order

| Operation | Python API route | Next action |
|---|---|---|
| CT+CT / CT-CT / CTxCT | native HEIR CKKS arithmetic | ready |
| SUM | native HEIR additions | ready as fixed public-width circuit |
| MEAN | encrypted SUM × public `1/N` | ready when `N` is public and fixed by packing |
| VAR | SUM, square/SUM, and two scalar multiplications | add only after the SUM/MEAN frontend example passes on the target HEIR version |
| MAX / MIN | OpenFHE CKKS↔FHEW comparison tree | separate Python wrapper; not a normal `@compile(scheme="ckks")` expression |

## What changes from benchmark code

1. Keep MLIR/CMake only as compiler-level regression tests.
2. Put user-visible feature functions in Python decorator modules.
3. Fix each circuit's packed width and public count before compilation.
4. Return ciphertexts from feature functions; do not decrypt between feature
   and aggregate stages.
5. Add a separate Python OpenFHE scheme-switching adapter for exact MIN/MAX.
   Do not pretend `max(a, b)` is ordinary CKKS arithmetic.

The benchmark scripts remain useful to validate latency and accuracy, but
application code should call the frontend functions rather than reproduce the
CMake harness.
