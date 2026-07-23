from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from code.heir.scripts.run_official_python_var_minmax_trial import (
    _sample_variance,
    _status,
)


class OfficialPythonVarMinMaxTrialTest(unittest.TestCase):
    def test_sample_variance_matches_expected(self):
        self.assertAlmostEqual(18580.0, _sample_variance([160, -100, 0, 60, 250]))

    def test_status_accepts_relative_or_absolute_contract(self):
        self.assertEqual(
            "PASS",
            _status(
                actual=250.5,
                expected=250.0,
                relative_tolerance=1e-5,
                absolute_tolerance=1.0,
            )[2],
        )
        self.assertEqual(
            "FAIL",
            _status(
                actual=253.0,
                expected=250.0,
                relative_tolerance=1e-5,
                absolute_tolerance=1.0,
            )[2],
        )


if __name__ == "__main__":
    unittest.main()
