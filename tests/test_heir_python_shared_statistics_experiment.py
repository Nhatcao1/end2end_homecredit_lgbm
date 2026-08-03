from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from code.heir_python.experiments.shared_payment_diff_statistics import (
    SharedPaymentDiffStatisticsProgram,
    payment_diff_statistics_mlir,
    read_prepared_payment_group,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data/prepared/examples/payment_diff_demo_group.csv"


class _Program:
    compilation_result = SimpleNamespace(
        arg_enc_funcs={"arg0": object(), "arg1": object()}
    )

    def setup(self):
        return None

    def encrypt_arg0(self, values):
        return values

    def encrypt_arg1(self, values):
        return values

    def eval(self, installment, payment):
        differences = [a - b for a, b in zip(installment, payment)]
        total = sum(differences)
        mean = total / 3
        variance = sum((value - mean) ** 2 for value in differences[:3]) / 2
        return [total, mean, variance]

    def decrypt_result(self, result):
        return result


class SharedStatisticsExperimentTest(unittest.TestCase):
    def test_mlir_has_one_shared_feature_and_one_tensor_result(self):
        source = payment_diff_statistics_mlir(8, 3)
        self.assertEqual(1, source.count("%payment_diff = arith.subf"))
        self.assertIn("tensor.from_elements", source)
        self.assertIn("return %statistics : tensor<3xf64>", source)

    def test_credit_fixture_runs_through_one_program(self):
        installment, payment = read_prepared_payment_group(FIXTURE)

        class NumPyDouble:
            float64 = float

            @staticmethod
            def zeros(width, dtype):
                del dtype
                return _WritableVector([0.0] * width)

            @staticmethod
            def asarray(values, dtype):
                del dtype
                return list(values)

        with patch(
            "code.heir_python.experiments.shared_payment_diff_statistics."
            "program._load_heir_compile",
            return_value=lambda **_: _Program(),
        ), patch.dict("sys.modules", {"numpy": NumPyDouble()}):
            program = SharedPaymentDiffStatisticsProgram(
                width=8,
                valid_count=3,
                input_scale=1.0,
            )
            program.setup()
            encrypted = program.encrypt_parents(installment, payment)
            observed = program.decrypt(program.eval(encrypted))

        self.assertEqual(60.0, observed.total)
        self.assertEqual(20.0, observed.mean)
        self.assertEqual(17200.0, observed.sample_variance)

    def test_experiment_has_no_legacy_or_direct_openfhe_path(self):
        folder = (
            ROOT
            / "code/heir_python/experiments/shared_payment_diff_statistics"
        )
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in folder.glob("*.py")
        )
        self.assertNotIn("code.heir.", source)
        self.assertNotIn("import openfhe", source)
        self.assertNotIn("subprocess", source)


class _WritableVector(list):
    def __setitem__(self, key, value):
        if isinstance(key, slice):
            value = list(value)
        super().__setitem__(key, value)


if __name__ == "__main__":
    unittest.main()
