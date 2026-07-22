from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from code.heir.prepared_installments import load_prepared_parents, public_power_of_two_scale


class PreparedInstallmentsTest(unittest.TestCase):
    def test_loads_only_real_valid_lanes_across_batches(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary); batches = root / "batches"; batches.mkdir()
            (batches / "batch_000000.csv").write_text("AMT_PAYMENT,AMT_INSTALMENT,valid\n1,2,1\n0,0,0\n", encoding="utf-8")
            (batches / "batch_000001.csv").write_text("AMT_PAYMENT,AMT_INSTALMENT,valid\n3,5,1\n", encoding="utf-8")
            result = load_prepared_parents(root, 2)
            self.assertEqual(result.payment, [1.0, 3.0])
            self.assertEqual(result.installment, [2.0, 5.0])
            self.assertEqual(result.valid, [1.0, 1.0])

    def test_scale_is_power_of_two_and_fits_values(self) -> None:
        self.assertEqual(public_power_of_two_scale([1000.0], [-20.0]), 2048.0)


if __name__ == "__main__":
    unittest.main()
