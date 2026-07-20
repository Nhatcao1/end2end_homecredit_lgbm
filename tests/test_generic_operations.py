from __future__ import annotations

import unittest

from code.heir.operations.benchmarking import run_expression_benchmark
from code.heir.operations.columns import (
    binary_mlir,
    binary_reference,
    prepare_nullable_column,
)
from code.heir.operations.contracts import operation_contract, require_implemented


class GenericOperationsTest(unittest.TestCase):
    def test_nullable_packing_does_not_derive_a_feature(self) -> None:
        values, mask = prepare_nullable_column([3.0, None, float("nan"), -2])
        self.assertEqual(values, [3.0, 0.0, 0.0, -2.0])
        self.assertEqual(mask, [1.0, 0.0, 0.0, 1.0])

    def test_generic_binary_oracles_and_mlir(self) -> None:
        self.assertEqual(binary_reference([4, 3], [1, 5], "subtract"), [3.0, -2.0])
        self.assertEqual(binary_reference([4, 3], [1, 5], "add"), [5.0, 8.0])
        self.assertEqual(binary_reference([4, 3], [1, 5], "multiply"), [4.0, 15.0])
        self.assertIn("arith.subf", binary_mlir(8, "subtract"))
        self.assertIn("tensor<8xf64>", binary_mlir(8, "multiply"))

    def test_deferred_operations_fail_before_feature_execution(self) -> None:
        for operation in ("divide", "mean", "variance", "threshold", "min_max"):
            self.assertNotEqual(operation_contract(operation).status, "implemented")
            with self.assertRaises(NotImplementedError):
                require_implemented(operation)

    def test_headline_excludes_encryption_and_decryption(self) -> None:
        result = run_expression_benchmark(
            python_calculation=lambda: [3.0, -2.0],
            encrypt=lambda: [4.0, 3.0, 1.0, 5.0],
            encrypted_calculation=lambda ciphertext: [ciphertext[0] - ciphertext[2], ciphertext[1] - ciphertext[3]],
            decrypt=lambda ciphertext: ciphertext,
        )
        self.assertEqual(result.max_absolute_error, 0.0)
        self.assertEqual(result.max_relative_error, 0.0)
        self.assertEqual(
            set(result.to_dict()["headline_timing"]),
            {"python_calculation_seconds", "encrypted_evaluation_seconds"},
        )


if __name__ == "__main__":
    unittest.main()
