from __future__ import annotations

import unittest
from pathlib import Path

from code.heir.scripts.run_ckks_variance_benchmark import raise_multiply_context_budget


class RunCkksVarianceBenchmarkTest(unittest.TestCase):
    def test_variance_uses_generated_square_sum_and_deep_context(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "code" / "heir" / "scripts" / "run_ckks_variance_benchmark.py").read_text(encoding="utf-8")
        self.assertIn("encrypted_multiply(context", source)
        self.assertIn("encrypted_sum(context, squares)", source)
        self.assertIn("EvalMult(mean, mean)", source)
        self.assertIn("--ckks-mul-depth", source)
        self.assertIn("CKKS-SQSUM-01", source)
        self.assertIn("CKKS-VAR-01", source)

    def test_context_patch(self) -> None:
        patched, original = raise_multiply_context_budget("params.SetMultiplicativeDepth(1);", 12)
        self.assertEqual(original, 1)
        self.assertEqual(patched, "params.SetMultiplicativeDepth(12);")


if __name__ == "__main__":
    unittest.main()
