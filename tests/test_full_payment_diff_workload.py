from __future__ import annotations

import unittest
from pathlib import Path


class FullPaymentDiffWorkloadTest(unittest.TestCase):
    def test_runner_uses_one_context_and_global_moments(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "code" / "heir" / "scripts" / "run_full_payment_diff_workload.py").read_text(encoding="utf-8")
        self.assertIn("fixed_count_sum_squares", source)
        self.assertIn("One CKKS context", source)
        self.assertIn("fullCount", source)
        self.assertIn("he_workload_seconds", source)
        self.assertIn("he_pipeline_seconds", source)
        self.assertIn("payment_diff_batches", source)
        self.assertIn("loadBundle(featurePath)", source)
        self.assertIn("addBalanced", source)
        self.assertIn("log2(N)", source)
        self.assertIn("--amount-scale", source)
        self.assertIn("amount /", source)
        self.assertNotIn("DEMO_ROWS", source)


if __name__ == "__main__":
    unittest.main()
