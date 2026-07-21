from __future__ import annotations

import unittest
from pathlib import Path


class InstallmentsWholeDataframeFlowTest(unittest.TestCase):
    def test_diagram_marks_scope_and_deferred_paths(self) -> None:
        diagram = (
            Path(__file__).resolve().parents[1]
            / "docs"
            / "INSTALLMENTS_WHOLE_DF_KERNEL_FLOW.mmd"
        ).read_text(encoding="utf-8")
        self.assertIn("no groupby", diagram)
        self.assertIn("K-RATIO", diagram)
        self.assertIn("K-SUBTRACT", diagram)
        self.assertIn("PAYMENT_PERC aggregation", diagram)
        self.assertIn("OpenFHE CKKS", diagram)


if __name__ == "__main__":
    unittest.main()
