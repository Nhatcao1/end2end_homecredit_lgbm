from __future__ import annotations

import unittest

from code.heir.examples.quick_installments_features import (
    expected_plaintext,
    payment_perc_newton_mlir,
    positive_difference_mlir,
)
from code.heir.kernels.sum import encrypted_sum_mlir
from code.heir.scripts.run_payment_features_ciphertext_demo import RUNNER


class QuickInstallmentsFeaturesTest(unittest.TestCase):
    def test_notebook_equivalent_tiny_expected_output(self) -> None:
        self.assertEqual(expected_plaintext(), [
            {"PAYMENT_PERC": 0.8, "PAYMENT_DIFF": 160.0, "DPD": 10.0, "DBD": 0.0},
            {"PAYMENT_PERC": 1.2, "PAYMENT_DIFF": -100.0, "DPD": 0.0, "DBD": 10.0},
            {"PAYMENT_PERC": 1.0, "PAYMENT_DIFF": 0.0, "DPD": 0.0, "DBD": 0.0},
        ])

    def test_mlir_exposes_approximation_and_generic_input_order(self) -> None:
        payment = payment_perc_newton_mlir(8)
        positive = positive_difference_mlir(8)
        self.assertIn("@payment_perc_newton", payment)
        self.assertIn("%inverse_normalized", payment)
        self.assertIn("@positive_difference_smoothstep", positive)
        self.assertIn("%raw_difference = arith.subf %l, %r", positive)
        self.assertIn("%range = arith.constant 10.0 : f64", positive)

    def test_payment_demo_adds_only_heir_sum(self) -> None:
        sum_source = encrypted_sum_mlir(8)
        self.assertIn("@encrypted_sum", sum_source)
        self.assertIn("arith.addf", sum_source)
        self.assertIn("auto encryptedResult", RUNNER)
        self.assertIn(
            "auto encryptedSum = encrypted_sum(context, encryptedResult);", RUNNER
        )
        self.assertIn("cannot save result ciphertext container", RUNNER)
        self.assertIn("cannot save sum ciphertext", RUNNER)
        self.assertNotIn("EvalSum", RUNNER)
        self.assertNotIn("encryptedMean", RUNNER)
        self.assertNotIn("encryptedVariance", RUNNER)


if __name__ == "__main__":
    unittest.main()
