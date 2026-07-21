from __future__ import annotations

import unittest

from code.heir.kernels.fixed_count_statistics import (
    fixed_count_statistics_mlir,
    fixed_count_statistics_reference,
)
from code.heir.scripts.run_payment_diff_fixed_count_aggregates import RUNNER


class PaymentDiffFixedCountAggregatesTest(unittest.TestCase):
    def test_plaintext_oracle(self) -> None:
        self.assertEqual(fixed_count_statistics_reference([160.0, -100.0, 0.0]), (60.0, 20.0, 17200.0))

    def test_mlir_uses_public_count_and_returns_three_encrypted_scalars(self) -> None:
        source = fixed_count_statistics_mlir(8, 3)
        self.assertIn("valid_count=3", source)
        self.assertIn("return %sum_result, %mean, %sample_variance", source)
        self.assertEqual(source.count("{secret.secret}"), 1)

    def test_runner_does_not_claim_max_is_ckks(self) -> None:
        self.assertIn("encryptedStats.arg2", RUNNER)
        self.assertIn("Save/audit the source branch", RUNNER)


if __name__ == "__main__":
    unittest.main()
