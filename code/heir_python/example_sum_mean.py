#!/usr/bin/env python3
"""Run SUM and MEAN through the small HEIR-Python session API."""

from __future__ import annotations

import argparse

from code.heir_python import HeirCkksSession


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--values",
        nargs="+",
        type=float,
        default=[160.0, -100.0, 0.0],
    )
    parser.add_argument("--width", type=int, default=8)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    session = HeirCkksSession(
        width=args.width,
        valid_count=len(args.values),
        debug=args.debug,
    )
    session.setup()

    # One logical input, represented by two HEIR-owned encrypted branches.
    encrypted_values = session.encrypt(args.values)
    encrypted_sum = session.sum(encrypted_values)
    encrypted_mean = session.mean(encrypted_values)

    # Decryption is explicit and occurs only at this final audit boundary.
    observed_sum = session.decrypt(encrypted_sum)
    observed_mean = session.decrypt(encrypted_mean)
    expected_sum = sum(args.values)
    expected_mean = expected_sum / len(args.values)

    print(f"Python SUM: {expected_sum}")
    print(f"HEIR CKKS SUM: {observed_sum}")
    print(f"Python MEAN: {expected_mean}")
    print(f"HEIR CKKS MEAN: {observed_mean}")
    print(
        "One ciphertext shared by SUM and MEAN: "
        f"{session.uses_one_ciphertext_for_both_aggregates}"
    )


if __name__ == "__main__":
    main()
