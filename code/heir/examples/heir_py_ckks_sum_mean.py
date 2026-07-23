#!/usr/bin/env python3
"""Run real SUM and MEAN through HEIR's official Python/OpenFHE API."""

from code.heir.python_api import compile_mean, compile_sum


def main() -> None:
    values = [160.0, -100.0, 0.0]
    width = 8

    # HEIR's current Python backend permits one ciphertext result per compiled
    # function, so SUM and MEAN are honest, independent application programs.
    sum_program = compile_sum(width=width, valid_count=len(values), debug=True)
    mean_program = compile_mean(width=width, valid_count=len(values), debug=True)
    sum_program.setup()
    mean_program.setup()

    encrypted_sum_input = sum_program.encrypt(values)
    encrypted_mean_input = mean_program.encrypt(values)
    encrypted_sum = sum_program.eval(encrypted_sum_input)
    encrypted_mean = mean_program.eval(encrypted_mean_input)

    # In an application, encrypted_sum/encrypted_mean may remain encrypted.
    # These decryptions are the explicit final audit boundary.
    he_sum = sum_program.decrypt(encrypted_sum)
    he_mean = mean_program.decrypt(encrypted_mean)
    expected_sum = sum(values)
    expected_mean = expected_sum / len(values)
    print(f"expected sum={expected_sum}, HE sum={he_sum}")
    print(f"expected mean={expected_mean}, HE mean={he_mean}")


if __name__ == "__main__":
    main()
