from __future__ import annotations

import unittest

from code.heir.scripts.run_ckks_fhew_minmax_benchmark import (
    RUNNER,
    _generated_values,
    _pad_with_real_candidate,
    _validate,
)


class CkksFhewMinMaxBenchmarkTest(unittest.TestCase):
    def test_uses_literal_openfhe_min_and_max_reductions(self) -> None:
        self.assertIn("EvalMinSchemeSwitching", RUNNER)
        self.assertIn("EvalMaxSchemeSwitching", RUNNER)
        self.assertIn("EvalCompareSwitchPrecompute(1, 1)", RUNNER)
        self.assertIn("SetComputeArgmin(true)", RUNNER)
        self.assertIn("if (argc != 7)", RUNNER)
        self.assertIn("numValues <= 4096", RUNNER)

    def test_100_real_values_are_padded_to_128_by_repeating_a_real_candidate(self) -> None:
        values = _generated_values(100, -100.0, 100.0, 7)
        _validate(values, 1024.0)
        padded, packing = _pad_with_real_candidate(values)
        self.assertEqual(len(padded), 128)
        self.assertEqual(packing["padding_count"], 28)
        self.assertEqual(padded[-1], values[0])
        self.assertEqual(min(padded), min(values))
        self.assertEqual(max(padded), max(values))

    def test_ties_are_valid_but_out_of_range_inputs_are_rejected(self) -> None:
        _validate([1.0, 1.0, 2.0, 3.0], 1024.0)
        with self.assertRaisesRegex(ValueError, "unit-circle"):
            _validate([-600.0, -1.0, 1.0, 2.0], 1024.0)


if __name__ == "__main__":
    unittest.main()
