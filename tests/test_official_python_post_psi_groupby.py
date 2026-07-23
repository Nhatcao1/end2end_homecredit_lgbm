import csv
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from code.heir.python_api.official_groupby import (
    OfficialPaymentDiffGroupStatistics,
    OfficialPaymentDiffGroupSum,
    OpaquePaymentGroup,
    payment_diff_statistics_mlir,
    payment_diff_sum_mlir,
    prepare_post_psi_groups,
)


class FakeProgram:
    compilation_result = SimpleNamespace(
        arg_enc_funcs={"arg0": object(), "arg1": object()},
    )

    def setup(self):
        return None

    def encrypt_arg0(self, value):
        return ("installment-ct", value)

    def encrypt_arg1(self, value):
        return ("payment-ct", value)

    def eval(self, *args):
        return ("sum-ct", args)

    def decrypt_result(self, value):
        return 60.0


class FakeStatisticsProgram(FakeProgram):
    def decrypt_result(self, value):
        return [60.0, 20.0, 17200.0]


class OfficialPythonPostPsiGroupbyTest(unittest.TestCase):
    def test_mlir_calculates_feature_after_encryption_then_sums(self):
        source = payment_diff_sum_mlir(4)
        self.assertIn(
            "%difference = arith.subf %installment, %payment",
            source,
        )
        self.assertIn("return %sum_result : f64", source)
        self.assertNotIn("SK_ID_CURR", source)

    def test_statistics_are_one_encrypted_tensor_result(self):
        source = payment_diff_statistics_mlir(4)
        self.assertIn(
            "%difference = arith.subf %installment, %payment",
            source,
        )
        self.assertIn(
            "%squares = arith.mulf %difference, %difference",
            source,
        )
        self.assertIn("%inverse_count: f64", source)
        self.assertIn(
            "return %result : tensor<3xf64>",
            source,
        )
        self.assertEqual(1, source.count("return "))

    def test_program_uses_two_compiled_encryptors(self):
        target = "code.heir.python_api.official_groupby._load_official_heir_compile"
        pack_target = "code.heir.python_api.official_groupby._pack_column"
        with patch(target, return_value=lambda **_: FakeProgram()):
            program = OfficialPaymentDiffGroupSum(width=4)
        program.setup()
        group = OpaquePaymentGroup(0, (640.0, 600.0), (800.0, 500.0))
        with patch(
            pack_target,
            side_effect=["installment-packed", "payment-packed"],
        ):
            encrypted = program.encrypt(group)
        self.assertEqual(
            (
                ("installment-ct", "installment-packed"),
                ("payment-ct", "payment-packed"),
            ),
            encrypted,
        )
        self.assertEqual(60.0, program.decrypt(program.eval(encrypted)))

    def test_statistics_program_reuses_two_parent_encryptors(self):
        target = "code.heir.python_api.official_groupby._load_official_heir_compile"
        pack_target = "code.heir.python_api.official_groupby._pack_column"
        with patch(target, return_value=lambda **_: FakeStatisticsProgram()):
            program = OfficialPaymentDiffGroupStatistics(
                width=4,
                input_scale=2.0,
            )
        program.setup()
        group = OpaquePaymentGroup(
            0,
            (640.0, 600.0, 1000.0),
            (800.0, 500.0, 1000.0),
        )
        with patch(
            pack_target,
            side_effect=["installment-packed", "payment-packed"],
        ):
            encrypted = program.encrypt(group)
        result = program.eval(encrypted, valid_count=3)
        self.assertEqual(
            (
                ("installment-ct", "installment-packed"),
                ("payment-ct", "payment-packed"),
                1.0 / 3.0,
                1.0 / 2.0,
            ),
            result[1],
        )
        self.assertEqual(
            (120.0, 40.0, 68800.0),
            program.decrypt(result),
        )

    def test_client_layout_consumes_only_post_psi_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bridge = root / "bridge" / "private_exchange"
            bridge.mkdir(parents=True)
            with (bridge / "sender_application_layout.csv").open(
                "w", encoding="utf-8", newline=""
            ) as handle:
                writer = csv.writer(handle)
                writer.writerow(["app_index", "SK_ID_CURR"])
                writer.writerows([[0, "100"], [1, "200"]])
            installments = root / "installments.csv"
            with installments.open(
                "w", encoding="utf-8", newline=""
            ) as handle:
                writer = csv.writer(handle)
                writer.writerow(
                    ["SK_ID_CURR", "AMT_PAYMENT", "AMT_INSTALMENT"]
                )
                writer.writerows(
                    [
                        ["100", 640, 800],
                        ["100", 600, 500],
                        ["200", 100, 120],
                        ["999", 1, 9999],
                    ]
                )
            layout = prepare_post_psi_groups(
                installments,
                root / "bridge",
                group_count=2,
                bucket_size=2,
            )

        self.assertEqual(2, layout.post_psi_applicants)
        self.assertEqual({1, 2}, {group.real_count for group in layout.groups})
        self.assertEqual(
            {"100", "200"},
            {row[1] for row in layout.private_mapping},
        )
        self.assertFalse(
            any(
                hasattr(group, "SK_ID_CURR") or hasattr(group, "sk_id_curr")
                for group in layout.groups
            )
        )


if __name__ == "__main__":
    unittest.main()
