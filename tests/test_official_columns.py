from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from code.heir.python_api.official_columns import (
    OfficialCkksBinaryColumn,
    OfficialCkksBinaryColumnAggregate,
    OfficialCkksBinaryColumnStatistics,
    binary_column_aggregate_mlir,
    binary_column_mlir,
    binary_column_statistics_mlir,
)


class FakeProgram:
    compilation_result = SimpleNamespace(
        arg_enc_funcs={"arg0": object(), "arg1": object()},
    )

    def setup(self):
        return None

    def encrypt_arg0(self, values):
        return ("left-ct", values)

    def encrypt_arg1(self, values):
        return ("right-ct", values)

    def eval(self, *args):
        return ("result-ct", args)


class FakeColumnProgram(FakeProgram):
    def decrypt_result(self, result):
        return [0.5, -0.25, 0.0, 0.0]


class FakeStatisticsProgram(FakeProgram):
    def decrypt_result(self, result):
        return [30.0, 15.0, 2.0]


class FakeAggregateProgram(FakeProgram):
    def decrypt_result(self, result):
        return 2.0


class OfficialColumnsTest(unittest.TestCase):
    def test_primitive_mlir_is_feature_agnostic(self):
        for operation, instruction in (
            ("add", "arith.addf"),
            ("subtract", "arith.subf"),
            ("multiply", "arith.mulf"),
        ):
            source = binary_column_mlir(4, operation)
            self.assertIn(instruction, source)
            self.assertNotIn("PAYMENT_DIFF", source)
            self.assertNotIn("AMT_", source)

    def test_generic_statistics_return_one_encrypted_tensor(self):
        source = binary_column_statistics_mlir(4, "subtract")
        self.assertIn("%derived = arith.subf %left, %right", source)
        self.assertIn("return %result : tensor<3xf64>", source)
        self.assertNotIn("PAYMENT_DIFF", source)

    def test_single_output_aggregate_mlir_avoids_multi_result_tensor(self):
        for aggregate, result_name in (
            ("sum", "%sum_result"),
            ("mean", "%mean_result"),
            ("variance", "%variance_result"),
        ):
            source = binary_column_aggregate_mlir(
                8,
                3,
                "subtract",
                aggregate,
            )
            self.assertIn("%derived = arith.subf %left, %right", source)
            self.assertIn(f"return {result_name} : f64", source)
            self.assertNotIn("tensor.from_elements", source)

    def test_binary_column_wraps_dataframe_style_sequences(self):
        loader = "code.heir.python_api.official_columns._load_official_heir_compile"
        packer = "code.heir.python_api.official_columns._pack"
        with patch(loader, return_value=lambda **_: FakeColumnProgram()):
            program = OfficialCkksBinaryColumn(
                "subtract",
                width=4,
                input_scale=2.0,
            )
        program.setup()
        with patch(packer, side_effect=["left-packed", "right-packed"]):
            encrypted = program.encrypt([10.0, 5.0], [3.0, 2.0])
        result = program.eval(encrypted)
        self.assertEqual((1.0, -0.5), program.decrypt(result, valid_count=2))

    def test_generic_statistics_rescale_sum_mean_and_variance(self):
        loader = "code.heir.python_api.official_columns._load_official_heir_compile"
        packer = "code.heir.python_api.official_columns._pack"
        with patch(loader, return_value=lambda **_: FakeStatisticsProgram()):
            program = OfficialCkksBinaryColumnStatistics(
                "subtract",
                width=4,
                input_scale=2.0,
            )
        program.setup()
        with patch(packer, side_effect=["left-packed", "right-packed"]):
            encrypted = program.encrypt([10.0, 5.0], [3.0, 2.0])
        result = program.eval(encrypted, valid_count=2)
        self.assertEqual(
            (60.0, 30.0, 8.0),
            program.decrypt(result),
        )
        self.assertEqual((0.5, 1.0), result[1][-2:])

    def test_single_output_variance_restores_squared_input_scale(self):
        loader = "code.heir.python_api.official_columns._load_official_heir_compile"
        with patch(loader, return_value=lambda **_: FakeAggregateProgram()):
            program = OfficialCkksBinaryColumnAggregate(
                "subtract",
                "variance",
                width=4,
                valid_count=2,
                input_scale=8.0,
            )
        program.setup()
        self.assertEqual(128.0, program.decrypt("encrypted-result"))


if __name__ == "__main__":
    unittest.main()
