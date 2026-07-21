from __future__ import annotations

import unittest
from pathlib import Path


class InspectInstallmentsGroupLayoutTest(unittest.TestCase):
    def test_inspector_is_client_only_and_does_not_encode_ciphertexts(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "code"
            / "heir"
            / "scripts"
            / "inspect_installments_group_layout.py"
        ).read_text(encoding="utf-8")
        self.assertIn("client_only_group_layout_inspected", source)
        self.assertIn("SK_ID_CURR", source)
        self.assertIn("candidate_public_bucket_coverage", source)
        self.assertNotIn("Encrypt(", source)


if __name__ == "__main__":
    unittest.main()
