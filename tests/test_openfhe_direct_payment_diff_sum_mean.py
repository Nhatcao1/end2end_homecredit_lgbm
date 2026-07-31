import csv
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from code.openfhe_direct import OpenFHECreditSession
from code.openfhe_direct.benchmarks.payment_diff_sum_mean import (
    load_raw_installment_parents,
    run_payment_diff_sum_mean_matrix,
)
from tests.test_openfhe_direct_credit_api import _OpenFHE


class OpenFHEDirectPaymentDiffSumMeanTest(unittest.TestCase):
    @staticmethod
    def _write_source(path: Path) -> None:
        path.write_text(
            "SK_ID_CURR,AMT_PAYMENT,AMT_INSTALMENT\n"
            "1,1,11\n"
            "2,,22\n"
            "3,2,22\n"
            "4,3,33\n"
            "5,4,44\n"
            "6,5,55\n",
            encoding="utf-8",
        )

    def test_raw_loader_removes_invalid_parent_rows(self):
        with TemporaryDirectory() as temporary:
            source = Path(temporary) / "installments.csv"
            self._write_source(source)

            loaded = load_raw_installment_parents(source, 5)

            self.assertEqual([11, 22, 33, 44, 55], loaded.installment)
            self.assertEqual([1, 2, 3, 4, 5], loaded.payment)
            self.assertEqual(6, loaded.raw_rows_examined)
            self.assertEqual(1, loaded.invalid_rows_dropped)

    def test_sum_and_mean_merge_two_ciphertext_chunks_globally(self):
        def factory(**kwargs):
            return OpenFHECreditSession(
                **kwargs,
                _openfhe_module=_OpenFHE(),
            )

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "installments.csv"
            self._write_source(source)
            expected = {"sum": 150.0, "mean": 30.0}

            for operation, wanted in expected.items():
                with self.subTest(operation=operation):
                    output = root / operation
                    result = run_payment_diff_sum_mean_matrix(
                        operation=operation,
                        installments_csv=source,
                        value_counts=[5],
                        slot_count=4,
                        repetitions=2,
                        multiplicative_depth=4,
                        scaling_mod_size=50,
                        first_mod_size=60,
                        ring_dimension=0,
                        absolute_tolerance=1e-9,
                        relative_tolerance=1e-9,
                        output_dir=output,
                        overwrite=False,
                        _session_factory=factory,
                    )

                    self.assertEqual("PASS", result["status"])
                    with (output / "rows_5/results.csv").open(
                        "r",
                        encoding="utf-8",
                        newline="",
                    ) as handle:
                        rows = list(csv.DictReader(handle))
                    self.assertEqual(2, int(rows[0]["ciphertext_chunks"]))
                    self.assertEqual(wanted, float(rows[0]["python_value"]))
                    self.assertEqual(wanted, float(rows[0]["he_value"]))

    def test_benchmark_uses_session_api_without_heir_or_eval_calls(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "code/openfhe_direct/benchmarks/payment_diff_sum_mean.py"
        ).read_text(encoding="utf-8")

        self.assertIn("OpenFHECreditSession", source)
        self.assertNotIn("import openfhe", source)
        self.assertNotIn("heir-opt", source)
        self.assertNotIn("EvalSum", source)
        self.assertNotIn("EvalAdd", source)


if __name__ == "__main__":
    unittest.main()
