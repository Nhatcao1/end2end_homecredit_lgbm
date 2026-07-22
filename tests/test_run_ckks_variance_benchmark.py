from __future__ import annotations

import unittest
from pathlib import Path

from code.heir.scripts.run_ckks_variance_benchmark import RUNNER

class RunCkksVarianceBenchmarkTest(unittest.TestCase):
    def test_variance_uses_generated_square_sum_and_coherent_deep_context(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "code" / "heir" / "scripts" / "run_ckks_variance_benchmark.py").read_text(encoding="utf-8")
        self.assertIn("encrypted_multiply(context", source)
        self.assertIn("encrypted_sum(context, squares)", source)
        self.assertIn("EvalMult(mean, mean)", source)
        self.assertIn("--ckks-mul-depth", source)
        self.assertIn("SetFirstModSize", source)
        self.assertIn("SetScalingModSize", source)
        self.assertIn("FLEXIBLEAUTOEXT", source)
        self.assertIn("CKKS-SQSUM-01", source)
        self.assertIn("CKKS-VAR-01", source)

    def test_context_metadata_literals_close_after_rendering(self) -> None:
        rendered = (RUNNER.replace("@SIZE@", "8192").replace("@DEPTH@", "12")
                    .replace("@FIRST_MOD_SIZE@", "60").replace("@SCALING_MOD_SIZE@", "50")
                    .replace("@RING_DIMENSION@", "32768"))
        self.assertIn('<< ",\\"multiplicative_depth\\":12,\\"first_mod_size\\":60"', rendered)
        self.assertIn('<< ",\\"scaling_mod_size\\":50"', rendered)

if __name__ == "__main__":
    unittest.main()
