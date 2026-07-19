from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from code.heir.kernel_artifacts import build_kernel_artifacts
from code.heir.kernels.difference_moments import (
    difference_moments_mlir,
    difference_moments_reference,
)
from code.heir.kernels.dot_product import dot_product_mlir, dot_product_reference
from code.heir.kernels.linear_score import linear_score_mlir, linear_score_reference
from code.heir.kernels.moments import moments_mlir, moments_reference
from code.heir.kernels.polynomial_score import (
    polynomial_score_mlir,
    polynomial_score_reference,
)
from code.heir.kernels.registry import kernel_contracts


class ReusableKernelReferenceTest(unittest.TestCase):
    def test_plaintext_oracles(self) -> None:
        values = [1.0, -2.0, 3.0, 99.0]
        mask = [1.0, 1.0, 1.0, 0.0]
        self.assertAlmostEqual(dot_product_reference(values, [0.5, -1, 0.25, 0]), 3.25)
        self.assertEqual(moments_reference(values, mask), (3.0, 2.0, 14.0))
        self.assertEqual(
            difference_moments_reference(
                [5.0, 6.0, 7.0, 8.0], [2.0, 7.0, 3.0, 100.0], mask
            ),
            (3.0, 6.0, 26.0),
        )
        self.assertAlmostEqual(
            linear_score_reference(values, [0.5, -1, 0.25, 0], 0.25), 3.5
        )
        self.assertAlmostEqual(polynomial_score_reference(0.5, [1, 2, 3]), 2.75)

    def test_invalid_shapes_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            dot_product_reference([1], [])
        with self.assertRaises(ValueError):
            moments_reference([], [])
        with self.assertRaises(ValueError):
            difference_moments_reference([1], [1, 2], [1])
        with self.assertRaises(ValueError):
            linear_score_reference([1], [1, 2])
        with self.assertRaises(ValueError):
            polynomial_score_reference(1, [2])

    def test_mlir_security_boundaries_and_names(self) -> None:
        self.assertEqual(dot_product_mlir(4).count("{secret.secret}"), 2)
        self.assertEqual(moments_mlir(4).count("{secret.secret}"), 2)
        self.assertEqual(difference_moments_mlir(4).count("{secret.secret}"), 3)

        linear = linear_score_mlir(4)
        self.assertIn("@linear_score_ct_pt", linear)
        self.assertEqual(linear.count("{secret.secret}"), 1)
        self.assertIn("%weights: tensor<4xf64>,", linear)

        polynomial = polynomial_score_mlir(3)
        self.assertIn("@polynomial_score", polynomial)
        self.assertEqual(polynomial.count("arith.mulf"), 3)
        self.assertEqual(polynomial.count("{secret.secret}"), 1)

    def test_registry_has_no_rule_or_tree_kernel(self) -> None:
        contracts = kernel_contracts()
        self.assertEqual(
            [contract.kernel_id for contract in contracts],
            ["K01", "K02", "K03", "S01", "S02"],
        )
        searchable = " ".join(
            f"{contract.name} {contract.operation}" for contract in contracts
        ).lower()
        self.assertNotIn("lightgbm", searchable)
        self.assertNotIn("tree", searchable)
        self.assertNotIn("threshold", searchable)

        sources = {
            "dot_product_ct_ct": dot_product_mlir(4),
            "moments": moments_mlir(4),
            "difference_moments": difference_moments_mlir(4),
            "linear_score_ct_pt": linear_score_mlir(4),
            "polynomial_score": polynomial_score_mlir(3),
        }
        for contract in contracts:
            self.assertIn(f"@{contract.entry_function}", sources[contract.name])
            self.assertTrue(contract.expected_evaluation_keys)


class KernelArtifactTest(unittest.TestCase):
    def test_artifacts_include_hashes_and_oracles(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "kernel_review"
            manifest = build_kernel_artifacts(output, vector_size=4, polynomial_degree=2)
            self.assertEqual(len(manifest["kernels"]), 5)
            self.assertEqual(len(manifest["mlir_sources"]), 5)
            self.assertEqual(
                manifest["execution_status"], "mlir_and_plaintext_oracle_only"
            )
            for source in manifest["mlir_sources"].values():
                self.assertEqual(len(source["sha256"]), 64)
                self.assertTrue((output / source["file"]).is_file())
            oracle = json.loads(
                (output / "plaintext_oracle.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                sorted(oracle["expected_outputs"]),
                sorted(manifest["mlir_sources"]),
            )


if __name__ == "__main__":
    unittest.main()
