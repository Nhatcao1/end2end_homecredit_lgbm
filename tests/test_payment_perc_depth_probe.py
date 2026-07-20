from __future__ import annotations

import unittest

from code.heir.scripts.run_payment_perc_depth_probe import (
    patch_translated_mul_depth,
    scheme_to_openfhe_option,
)


class PaymentPercDepthProbeTest(unittest.TestCase):
    def test_legacy_pipeline_only_receives_entry_function(self) -> None:
        option = scheme_to_openfhe_option(
            mul_depth=12, first_mod_size=0, scaling_mod_size=0
        )
        self.assertEqual(
            option,
            "--scheme-to-openfhe=entry-function=payment_perc_newton",
        )

    def test_depth_is_patched_in_translated_openfhe_context(self) -> None:
        patched, inferred = patch_translated_mul_depth(
            "params.SetMultiplicativeDepth(8);", requested_depth=12
        )
        self.assertEqual(inferred, 8)
        self.assertEqual(patched, "params.SetMultiplicativeDepth(12);")


if __name__ == "__main__":
    unittest.main()
