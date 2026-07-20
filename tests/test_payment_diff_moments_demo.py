from __future__ import annotations

import unittest

from code.heir.scripts.run_payment_diff_moments_demo import RUNNER


class PaymentDiffMomentsDemoTest(unittest.TestCase):
    def test_generated_multiple_returns_use_heir_struct_fields(self) -> None:
        self.assertIn("encryptedMoments.arg0", RUNNER)
        self.assertIn("encryptedFinal.arg1", RUNNER)
        self.assertNotIn("std::get<0>(encryptedMoments)", RUNNER)


if __name__ == "__main__":
    unittest.main()
