from __future__ import annotations

import unittest
from pathlib import Path

from code.heir.scripts.run_payment_diff_max_openfhe_demo import RUNNER


class PaymentDiffMaxSchemeSwitchTest(unittest.TestCase):
    def test_max_uses_openfhe_switching_and_does_not_retain_argmax(self) -> None:
        self.assertIn("EvalMaxSchemeSwitching", RUNNER)
        self.assertIn("maxAndArgmax[0]", RUNNER)
        self.assertIn("not saved or decrypted", RUNNER)

    def test_mermaid_diagram_records_the_two_session_boundary(self) -> None:
        diagram = (Path(__file__).resolve().parents[1] / "docs" / "PAYMENT_DIFF_CIPHERTEXT_FLOW.mmd").read_text()
        self.assertIn("Ordinary HEIR CKKS session", diagram)
        self.assertIn("Dedicated OpenFHE CKKS", diagram)
        self.assertIn("cannot cross session", diagram)


if __name__ == "__main__":
    unittest.main()
