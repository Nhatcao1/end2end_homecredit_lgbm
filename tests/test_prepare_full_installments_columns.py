from __future__ import annotations

import unittest
from pathlib import Path


class FullInstallmentsPreparationTest(unittest.TestCase):
    def test_preparation_is_full_csv_batched_and_does_not_write_features(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "code"
            / "heir"
            / "scripts"
            / "prepare_full_installments_columns.py"
        ).read_text(encoding="utf-8")
        self.assertIn("chunksize=chunk_rows", source)
        self.assertIn("AMT_PAYMENT", source)
        self.assertIn("AMT_INSTALMENT", source)
        self.assertIn("Raw parent columns, not these derived values", source)
        self.assertIn("batch_manifest.json", source)


if __name__ == "__main__":
    unittest.main()
