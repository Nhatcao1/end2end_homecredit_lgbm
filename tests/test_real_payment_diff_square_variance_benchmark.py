import unittest
import inspect

from code.heir.scripts import run_real_payment_diff_square_variance_benchmark as benchmark


class RealPaymentDiffSquareVarianceBenchmarkTest(unittest.TestCase):
    def test_uses_generic_subtract_multiply_and_sum_without_intermediate_decrypt(self):
        self.assertIn("encrypted_subtract(context, encryptedInstallment, encryptedPayment)", benchmark.RUNNER)
        self.assertIn("encrypted_multiply(context, diffForSquare, diffForSquare)", benchmark.RUNNER)
        self.assertIn("encrypted_sum(context, squared)", benchmark.RUNNER)
        self.assertIn("EvalSub(secondMoment, meanSquared)", benchmark.RUNNER)
        self.assertIn("EvalMult(populationVariance", benchmark.RUNNER)

    def test_requires_a_depth_margin_for_variance(self):
        with self.assertRaisesRegex(ValueError, "at least 5"):
            benchmark.patch_multiply_depth("params.SetMultiplicativeDepth(1);", 4)
        patched, original = benchmark.patch_multiply_depth("params.SetMultiplicativeDepth(1);", 6)
        self.assertEqual(original, 1)
        self.assertIn("SetMultiplicativeDepth(6)", patched)

    def test_audit_status_rejects_large_relative_error(self):
        error, status = benchmark.tolerance_status(105.0, 100.0, 1e-3)
        self.assertEqual(error, 5.0)
        self.assertTrue(status.startswith("FAIL"))

    def test_report_passes_its_declared_tolerance_to_each_audit(self):
        source = inspect.getsource(benchmark.report)
        self.assertIn("float(reference[pandas_field]), relative_tolerance", source)


if __name__ == "__main__":
    unittest.main()
