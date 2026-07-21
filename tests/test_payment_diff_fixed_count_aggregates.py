from __future__ import annotations

import unittest

from code.heir.kernels.fixed_count_statistics import (
    fixed_count_mean_mlir,
    fixed_count_statistics_reference,
    fixed_count_sum_mlir,
    fixed_count_variance_mlir,
)
from code.heir.scripts.run_payment_diff_fixed_count_aggregates import RUNNER


class PaymentDiffFixedCountAggregatesTest(unittest.TestCase):
    def test_plaintext_oracle(self) -> None:
        self.assertEqual(fixed_count_statistics_reference([160.0, -100.0, 0.0]), (60.0, 20.0, 17200.0))

    def test_mlir_uses_public_count_and_one_output_per_branch(self) -> None:
        sources = [fixed_count_sum_mlir(8, 3), fixed_count_mean_mlir(8, 3), fixed_count_variance_mlir(8, 3)]
        self.assertTrue(all("tensor<8xf64> {secret.secret}" in source for source in sources))
        self.assertIn("return %sum_result", sources[0])
        self.assertIn("return %mean", sources[1])
        self.assertIn("return %sample_variance", sources[2])

    def test_runner_does_not_claim_max_is_ckks(self) -> None:
        self.assertIn('stage == "init"', RUNNER)
        self.assertIn('stage == "variance"', RUNNER)
        self.assertIn("loadEvaluationKeys", RUNNER)
        self.assertIn("using CiphertextBundle", RUNNER)
        self.assertIn("Sum uses additions only", RUNNER)


if __name__ == "__main__":
    unittest.main()
