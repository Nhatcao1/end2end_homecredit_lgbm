from __future__ import annotations

import unittest

from code.heir.operations.generic_api import encrypted_aggregation, encrypted_column


class GenericKernelApiTest(unittest.TestCase):
    def test_generic_column_operations_are_not_payment_specific(self) -> None:
        ratio = encrypted_column("ratio", 8)
        subtraction = encrypted_column("subtract", 8)
        self.assertIn("@encrypted_ratio_newton", ratio.mlir or "")
        self.assertIn("@encrypted_subtract", subtraction.mlir or "")

    def test_aggregation_routes_are_explicit(self) -> None:
        self.assertEqual(encrypted_aggregation("sum", 8, 3).status, "implemented")
        self.assertEqual(encrypted_aggregation("var", 8, 3).status, "implemented_with_public_count")
        self.assertEqual(encrypted_aggregation("max", 8, 3).route, "OpenFHE CKKS↔FHEW")


if __name__ == "__main__":
    unittest.main()
