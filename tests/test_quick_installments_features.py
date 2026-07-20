from __future__ import annotations

import unittest
from pathlib import Path

from code.heir.examples.quick_installments_features import (
    expected_plaintext,
    payment_perc_newton_mlir,
    positive_difference_mlir,
)
from code.heir.kernels.sum import encrypted_sum_mlir
from code.heir.scripts.run_payment_features_ciphertext_demo import RUNNER
from code.heir.scripts.run_payment_features_ciphertext_demo import run


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
        self.assertEqual(sum_source.count("arith.addf"), 7)
        self.assertIn("%index_0 = arith.constant 0 : index", sum_source)
        self.assertIn("tensor.extract %values[%index_0]", sum_source)
        self.assertNotIn("arith.constant 0.0", sum_source)
        self.assertNotIn("affine.for", sum_source)
        self.assertIn("auto encryptedResult", RUNNER)
        self.assertIn(
            "auto encryptedSum = encrypted_sum(context, encryptedResult);", RUNNER
        )
        self.assertLess(
            RUNNER.index("auto decrypted = @ENTRY@__decrypt__result0"),
            RUNNER.index("auto encryptedSum = encrypted_sum(context, encryptedResult);"),
        )
        self.assertLess(
            RUNNER.index("SerializeToFile(argv[@RESULT_CT@], encryptedResult"),
            RUNNER.index("auto encryptedSum = encrypted_sum(context, encryptedResult);"),
        )
        self.assertIn("cannot save result ciphertext container", RUNNER)
        self.assertIn("cannot save sum ciphertext", RUNNER)
        self.assertNotIn("EvalSum", RUNNER)
        self.assertNotIn("encryptedMean", RUNNER)
        self.assertNotIn("encryptedVariance", RUNNER)

    def test_failed_command_reports_exit_code(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "exit code"):
            run(["false"], Path.cwd())


if __name__ == "__main__":
    unittest.main()
