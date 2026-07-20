from __future__ import annotations

import unittest

from code.heir.scripts.run_payment_perc_depth_probe import scheme_to_openfhe_option


class PaymentPercDepthProbeTest(unittest.TestCase):
    def test_explicit_mul_depth_is_sent_to_the_heir_pipeline(self) -> None:
        option = scheme_to_openfhe_option(
            mul_depth=12, first_mod_size=0, scaling_mod_size=0
        )
        self.assertEqual(
            option,
            "--scheme-to-openfhe=entry-function=payment_perc_newton mul-depth=12",
        )

    def test_optional_modulus_sizes_are_explicit_when_requested(self) -> None:
        option = scheme_to_openfhe_option(
            mul_depth=12, first_mod_size=60, scaling_mod_size=50
        )
        self.assertIn("first-mod-size=60", option)
        self.assertIn("scaling-mod-size=50", option)


if __name__ == "__main__":
    unittest.main()
