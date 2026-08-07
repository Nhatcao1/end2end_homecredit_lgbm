from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from code.openfhe_direct.benchmarks.synthetic_numeric.multiplication_limits import (
    run_probe,
)


class _CkksDouble:
    def __init__(self, **options):
        self.options = options

    def evaluator_view(self):
        return self

    def encrypt(self, values):
        return list(values)

    def multiply(self, left, right):
        return [a * b for a, b in zip(left, right)]

    def decrypt(self, encrypted):
        return list(encrypted)


class _BgvDouble:
    def __init__(self, *, plaintext_modulus, **options):
        self.modulus = plaintext_modulus
        self.capacity = plaintext_modulus // 2
        self.options = options

    def evaluator_view(self):
        return self

    def encrypt(self, values):
        if any(abs(value) >= self.capacity for value in values):
            raise ValueError("outside centered plaintext range")
        return list(values)

    def multiply(self, left, right):
        result = []
        for first, second in zip(left, right):
            value = first * second % self.modulus
            result.append(
                value - self.modulus if value > self.capacity else value
            )
        return result

    def decrypt(self, encrypted):
        return list(encrypted)


class OpenFHEMultiplicationLimitsTest(unittest.TestCase):
    def test_probe_distinguishes_modular_wrap_from_ckks_approximation(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "result"
            result = run_probe(
                output_dir=root,
                repetitions=1,
                overwrite=False,
                _ckks_factory=_CkksDouble,
                _bgv_factory=_BgvDouble,
            )

            self.assertEqual("PROBE_EXECUTED", result["status"])
            rows = (root / "results.csv").read_text(encoding="utf-8")
            self.assertIn("MODULAR_WRAP", rows)
            self.assertIn("INPUT_OR_EVALUATION_REJECTED", rows)
            self.assertIn("ABSOLUTE_AND_RELATIVE_PASS", rows)
            report = (root / "REPORT.md").read_text(encoding="utf-8")
            self.assertIn("one_fresh_ct_x_ct", report)
            self.assertIn("10000000", report)
            self.assertIn("1000000000", report)


if __name__ == "__main__":
    unittest.main()
