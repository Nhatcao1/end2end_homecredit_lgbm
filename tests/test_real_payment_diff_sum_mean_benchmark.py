import unittest

from code.heir.scripts import run_real_payment_diff_sum_mean_benchmark as benchmark


class RealPaymentDiffSumMeanBenchmarkTest(unittest.TestCase):
    def test_runner_derives_feature_then_reuses_encrypted_sum_for_mean(self):
        self.assertIn("if (argc != 6) return 2;", benchmark.RUNNER)
        self.assertIn("std::ofstream out(argv[4])", benchmark.RUNNER)
        self.assertIn("std::ofstream meta(argv[5])", benchmark.RUNNER)
        self.assertIn("encrypted_subtract(context, encryptedInstallment, encryptedPayment)", benchmark.RUNNER)
        self.assertIn("auto partialSum = encrypted_sum(context, diffForSum)", benchmark.RUNNER)
        self.assertIn('#include "group_statistics.h"', benchmark.RUNNER)
        self.assertIn("heir::runtime::add_bundles", benchmark.RUNNER)
        self.assertIn("heir::runtime::mean_from_sum(context, totalSum, count)", benchmark.RUNNER)
        self.assertIn("auto encryptedValid = encrypted_sum__encrypt__arg0", benchmark.RUNNER)

    def test_mean_context_depth_patch_is_explicit(self):
        patched, original = benchmark.patch_mean_depth("parameters.SetMultiplicativeDepth(1);", 4)
        self.assertEqual(original, 1)
        self.assertIn("SetMultiplicativeDepth(4)", patched)

    def test_emitted_modulus_settings_are_reported_when_available(self):
        source = "params.SetFirstModSize(60); params.SetScalingModSize(50);"
        self.assertEqual(benchmark.emitted_parameter(source, "SetFirstModSize"), 60)
        self.assertEqual(benchmark.emitted_parameter(source, "SetScalingModSize"), 50)


if __name__ == "__main__":
    unittest.main()
