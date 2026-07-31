import csv
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from code.openfhe_direct import OpenFHECreditSession
from code.openfhe_direct.benchmarks.synthetic_vnd.generate_dataset import (
    generate_dataset,
)
from code.openfhe_direct.benchmarks.synthetic_vnd.add import (
    run_add_matrix,
)
from tests.test_openfhe_direct_credit_api import _OpenFHE


class OpenFHEDirectSyntheticVndAddTest(unittest.TestCase):
    def test_generator_is_prefix_consistent_and_within_vnd_range(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "data"
            result = generate_dataset(
                output_dir=root,
                value_counts=[2, 5],
                minimum_value=100_000,
                maximum_value=200_000_000,
                seed=123,
                overwrite=False,
            )

            def rows(path):
                with path.open("r", encoding="utf-8", newline="") as handle:
                    return list(csv.DictReader(handle))

            small = rows(root / "vnd_pairs_2.csv")
            large = rows(root / "vnd_pairs_5.csv")
            self.assertEqual(small, large[:2])
            self.assertTrue(result["prefix_consistent"])
            for row in large:
                self.assertGreaterEqual(int(row["LEFT_VALUE"]), 100_000)
                self.assertLessEqual(int(row["RIGHT_VALUE"]), 200_000_000)

    def test_ct_add_reports_magnitude_latency_and_accuracy(self):
        class Array(list):
            def tolist(self):
                return list(self)

        class NumPyDouble:
            float64 = float

            @staticmethod
            def asarray(values, dtype):
                del dtype
                return Array(values)

            @staticmethod
            def add(left, right):
                return Array(a + b for a, b in zip(left, right))

        def factory(**kwargs):
            return OpenFHECreditSession(
                **kwargs,
                _openfhe_module=_OpenFHE(),
            )

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data"
            generate_dataset(
                output_dir=data,
                value_counts=[5],
                minimum_value=100_000,
                maximum_value=200_000_000,
                seed=123,
                overwrite=False,
            )
            with patch.dict("sys.modules", {"numpy": NumPyDouble()}):
                result = run_add_matrix(
                    dataset_dir=data,
                    value_counts=[5],
                    slot_count=4,
                    repetitions=2,
                    multiplicative_depth=2,
                    scaling_mod_size=50,
                    first_mod_size=60,
                    ring_dimension=0,
                    absolute_tolerance=1e-9,
                    relative_tolerance=1e-9,
                    output_dir=root / "result",
                    overwrite=False,
                    _session_factory=factory,
                )

            self.assertEqual("PASS", result["status"])
            report = (root / "result/values_5/REPORT.md").read_text()
            self.assertIn("Vector length", report)
            self.assertNotIn("Rows:", report)
            self.assertIn("CT+CT", report)
            self.assertIn("numpy.add(float64)", report)
            self.assertIn("Expected-result range", report)
            self.assertIn("MAE", report)
            self.assertIn("HE online", report)

    def test_benchmark_calls_session_only(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "code/openfhe_direct/benchmarks/synthetic_vnd/add.py"
        ).read_text(encoding="utf-8")

        self.assertIn("OpenFHECreditSession", source)
        self.assertNotIn("import openfhe", source)
        self.assertNotIn("EvalAdd", source)
        self.assertNotIn("heir-opt", source)
        self.assertNotIn("PAYMENT_DIFF", source)


if __name__ == "__main__":
    unittest.main()
