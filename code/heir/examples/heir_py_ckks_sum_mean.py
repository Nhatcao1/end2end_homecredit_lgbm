#!/usr/bin/env python3
"""Small user-facing HEIR Python example: encrypted SUM and MEAN.

This is intentionally a usage example, not a benchmark. It requires the
official HEIR Python frontend, installed with::

    python3 -m pip install "heir_py[python,openfhe]"
"""

from __future__ import annotations

try:
    from heir import compile
    from heir.mlir import F64, Secret
except ImportError as error:
    raise SystemExit(
        "This example needs HEIR's Python frontend. Install it in a separate "
        "environment with: python3 -m pip install 'heir_py[python,openfhe]'"
    ) from error


WIDTH = 4  # Public circuit shape; change only by recompiling the function.


@compile(scheme="ckks", debug=True)
def encrypted_sum_and_mean(
    x0: Secret[F64],
    x1: Secret[F64],
    x2: Secret[F64],
    x3: Secret[F64],
):
    """Return encrypted ``sum(x)`` and encrypted ``sum(x) / 4``."""
    left_sum = x0 + x1
    right_sum = x2 + x3
    encrypted_sum = left_sum + right_sum
    encrypted_mean = encrypted_sum * (1.0 / WIDTH)
    return encrypted_sum, encrypted_mean


def main() -> None:
    values = (640.0, 600.0, 1000.0, 800.0)
    encrypted_sum_and_mean.setup()
    encrypted_values = [
        encrypted_sum_and_mean.encrypt_x0(values[0]),
        encrypted_sum_and_mean.encrypt_x1(values[1]),
        encrypted_sum_and_mean.encrypt_x2(values[2]),
        encrypted_sum_and_mean.encrypt_x3(values[3]),
    ]
    encrypted_result = encrypted_sum_and_mean.eval(*encrypted_values)
    he_sum, he_mean = encrypted_sum_and_mean.decrypt_result(encrypted_result)
    expected_sum = sum(values)
    expected_mean = expected_sum / WIDTH
    print(f"expected sum={expected_sum}, HE sum={he_sum}")
    print(f"expected mean={expected_mean}, HE mean={he_mean}")


if __name__ == "__main__":
    main()
