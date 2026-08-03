#!/usr/bin/env python3
"""Try one shared HEIR statistics program on prepared credit-rating data."""

from __future__ import annotations

import argparse
from pathlib import Path

from .program import (
    SharedPaymentDiffStatisticsProgram,
    read_prepared_payment_group,
)


DEFAULT_INPUT = Path("data/prepared/examples/payment_diff_demo_group.csv")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--width", type=int, default=8)
    parser.add_argument("--input-scale", type=float, default=2048.0)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    installment, payment = read_prepared_payment_group(args.input)
    differences = [due - paid for due, paid in zip(installment, payment)]
    expected_sum = sum(differences)
    expected_mean = expected_sum / len(differences)
    expected_variance = sum(
        (value - expected_mean) ** 2 for value in differences
    ) / (len(differences) - 1)

    program = SharedPaymentDiffStatisticsProgram(
        width=args.width,
        valid_count=len(differences),
        input_scale=args.input_scale,
        debug=args.debug,
    )
    program.setup()

    encrypted_parents = program.encrypt_parents(installment, payment)
    encrypted_statistics = program.eval(encrypted_parents)
    observed = program.decrypt(encrypted_statistics)

    print(f"Rows: {len(differences)}")
    print(f"Python PAYMENT_DIFF SUM: {expected_sum}")
    print(f"HEIR PAYMENT_DIFF SUM: {observed.total}")
    print(f"Python PAYMENT_DIFF MEAN: {expected_mean}")
    print(f"HEIR PAYMENT_DIFF MEAN: {observed.mean}")
    print(f"Python PAYMENT_DIFF sample VARIANCE: {expected_variance}")
    print(f"HEIR PAYMENT_DIFF sample VARIANCE: {observed.sample_variance}")
    print("Shared HEIR context: True")
    print("Parent ciphertexts encrypted once: True")
    print("Encrypted result tensor: [SUM, MEAN, VARIANCE]")


if __name__ == "__main__":
    main()
