from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from code.heir.scripts.run_ckks_fhew_minmax_benchmark import (
    RUNNER,
    _pad_with_real_candidate,
    _read_prepared_values,
    _resolve_input_scale,
    _validate,
)


class CkksFhewMinMaxBenchmarkTest(unittest.TestCase):
    def test_uses_literal_openfhe_min_and_max_reductions(self) -> None:
        self.assertIn("EvalMinSchemeSwitching", RUNNER)
        self.assertIn("EvalMaxSchemeSwitching", RUNNER)
        self.assertIn("EvalCompareSwitchPrecompute(1, 1, true)", RUNNER)
        self.assertIn("SetComputeArgmin(true)", RUNNER)
        self.assertIn("if (argc != 8)", RUNNER)
        self.assertIn("ringDimension >= 2 * numValues", RUNNER)

    def test_100_prepared_real_values_are_padded_to_128_by_repeating_a_real_candidate(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "batch.csv"
            path.write_text(
                "AMT_PAYMENT,AMT_INSTALMENT,valid\n"
                + "\n".join(f"{index + 0.5},{index + 1.0},1" for index in range(100))
                + "\n0,0,0\n",
                encoding="utf-8",
            )
            values = _read_prepared_values(path, "AMT_PAYMENT", 100)
        _validate(values, 1024.0)
        padded, packing = _pad_with_real_candidate(values)
        self.assertEqual(len(padded), 128)
        self.assertEqual(packing["padding_count"], 28)
        self.assertEqual(padded[-1], values[0])
        self.assertEqual(min(padded), min(values))
        self.assertEqual(max(padded), max(values))

    def test_auto_scale_is_public_power_of_two_that_fits_loaded_values(self) -> None:
        scale, policy = _resolve_input_scale([-10.0, 1000.0], 0.0)
        self.assertEqual(scale, 2048.0)
        self.assertIn("auto-selected", policy)

    def test_ties_are_valid_but_out_of_range_inputs_are_rejected(self) -> None:
        _validate([1.0, 1.0, 2.0, 3.0], 1024.0)
        with self.assertRaisesRegex(ValueError, "unit-circle"):
            _validate([-600.0, -1.0, 1.0, 2.0], 1024.0)

    def test_5000_real_values_fit_a_16384_ring_after_power_of_two_padding(self) -> None:
        values = [float(index) for index in range(5000)]
        padded, packing = _pad_with_real_candidate(values, candidate_capacity=8192)
        self.assertEqual(len(padded), 8192)
        self.assertEqual(packing["padding_count"], 3192)


if __name__ == "__main__":
    unittest.main()
