from __future__ import annotations

import unittest

from code.heir.scripts.run_ckks_fhew_minmax_benchmark import DEFAULT_VALUES, RUNNER, _validate


class CkksFhewMinMaxBenchmarkTest(unittest.TestCase):
    def test_uses_literal_openfhe_min_and_max_reductions(self) -> None:
        self.assertIn("EvalMinSchemeSwitching", RUNNER)
        self.assertIn("EvalMaxSchemeSwitching", RUNNER)
        self.assertIn("EvalCompareSwitchPrecompute(1, 1)", RUNNER)
        self.assertIn("SetComputeArgmin(true)", RUNNER)
        self.assertIn("if (argc != 6)", RUNNER)

    def test_default_values_meet_unit_circle_contract(self) -> None:
        _validate(list(DEFAULT_VALUES), 1024.0)

    def test_ties_are_valid_but_out_of_range_inputs_are_rejected(self) -> None:
        _validate([1.0, 1.0, 2.0, 3.0], 1024.0)
        with self.assertRaisesRegex(ValueError, "unit-circle"):
            _validate([-600.0, -1.0, 1.0, 2.0], 1024.0)


if __name__ == "__main__":
    unittest.main()
