from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from code.openfhe_direct.benchmarks.single_function import (
    run_batch_benchmark,
)
from code.openfhe_direct.prepared_data import load_prepared_parent_columns
from tests.test_openfhe_direct_credit_api import _OpenFHE


class OpenFHEDirectBatchBenchmarkTest(unittest.TestCase):
    def test_one_function_streams_multiple_ciphertext_chunks(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            batches = root / "prepared/batches"
            batches.mkdir(parents=True)
            (batches / "batch_000000.csv").write_text(
                "AMT_PAYMENT,AMT_INSTALMENT,valid\n"
                "1,11,1\n"
                "2,22,1\n"
                "3,33,1\n"
                "4,44,1\n"
                "5,55,1\n"
                "0,0,0\n",
                encoding="utf-8",
            )

            result = run_batch_benchmark(
                function="sum",
                prepared_dir=root / "prepared",
                value_count=5,
                slot_count=4,
                repetitions=2,
                multiplicative_depth=4,
                ring_dimension=0,
                absolute_tolerance=1e-9,
                relative_tolerance=1e-9,
                output_dir=root / "result",
                overwrite=False,
                _openfhe_module=_OpenFHE(),
            )

            self.assertEqual("PASS", result["status"])
            self.assertEqual(2, result["ciphertext_chunks"])
            self.assertEqual("sum", result["function"])
            self.assertTrue((root / "result/REPORT.md").is_file())

    def test_direct_loader_reads_only_requested_valid_rows(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            batches = root / "batches"
            batches.mkdir()
            (batches / "batch_000000.csv").write_text(
                "AMT_PAYMENT,AMT_INSTALMENT,valid\n"
                "1,2,1\n"
                "0,0,0\n"
                "3,4,1\n",
                encoding="utf-8",
            )

            result = load_prepared_parent_columns(root, 2)

            self.assertEqual([1.0, 3.0], result.payment)
            self.assertEqual([2.0, 4.0], result.installment)


if __name__ == "__main__":
    unittest.main()
