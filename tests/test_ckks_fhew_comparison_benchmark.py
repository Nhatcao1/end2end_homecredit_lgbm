from __future__ import annotations

import unittest
from pathlib import Path

from code.heir.scripts.run_ckks_fhew_comparison_benchmark import (
    DEFAULT_LEFT,
    DEFAULT_RIGHT,
    RUNNER,
    _assert_input_contract,
    _classification_rows,
)


class CkksFhewComparisonBenchmarkTest(unittest.TestCase):
    def test_direct_openfhe_comparison_and_fhew_setup_are_explicit(self) -> None:
        self.assertIn("EvalCompareSwitchPrecompute", RUNNER)
        self.assertIn("EvalCompareSchemeSwitching", RUNNER)
        self.assertIn("BTKeyGen", RUNNER)
        self.assertIn("SCHEMESWITCH", RUNNER)

    def test_default_cases_have_a_margin_and_expected_orientation(self) -> None:
        _assert_input_contract(list(DEFAULT_LEFT), list(DEFAULT_RIGHT), 0.0, 0.25)
        rows = _classification_rows([-1.0, 2.0], [0.0, 1.0], [0.99, 0.01], "test", 0.25)
        self.assertTrue(rows[0]["he_left_less_than_right"])
        self.assertFalse(rows[1]["he_left_less_than_right"])
        self.assertTrue(all(bool(row["match"]) for row in rows))

    def test_zero_boundary_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "minimum comparison margin"):
            _assert_input_contract([0.0] * 4, [0.0] * 4, 1.0, 0.25)

    def test_report_calls_out_separate_session_boundary(self) -> None:
        script = Path(__file__).resolve().parents[1] / "code" / "heir" / "scripts" / "run_ckks_fhew_comparison_benchmark.py"
        self.assertIn("cannot be mixed with ciphertexts from the ordinary HEIR CKKS context", script.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
