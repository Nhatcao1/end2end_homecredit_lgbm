from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from code.openfhe_direct import OpenFHECreditSession
from code.openfhe_direct.benchmarks.synthetic_vnd.ckks_sum import (
    run_ckks_sum_matrix,
)
from code.openfhe_direct.benchmarks.synthetic_vnd.generate_dataset import (
    generate_dataset,
)
from tests.test_openfhe_direct_credit_api import _OpenFHE


class OpenFHEDirectSyntheticVndCkksSumTest(unittest.TestCase):
    def test_ckks_sum_uses_same_generated_vector(self):
        class NumPyDouble:
            int64 = int

            @staticmethod
            def asarray(values, dtype):
                del dtype
                return list(values)

            @staticmethod
            def sum(values, dtype):
                del dtype
                return sum(values)

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
                maximum_value=20_000_000,
                seed=123,
                overwrite=False,
            )
            with patch.dict("sys.modules", {"numpy": NumPyDouble()}):
                result = run_ckks_sum_matrix(
                    dataset_dir=data,
                    value_counts=[5],
                    slot_count=8,
                    repetitions=2,
                    normalization_divisor=1_000_000.0,
                    multiplicative_depth=2,
                    scaling_mod_size=50,
                    first_mod_size=60,
                    ring_dimension=0,
                    absolute_tolerance_vnd=1e-6,
                    relative_tolerance=1e-12,
                    output_dir=root / "result",
                    overwrite=False,
                    _session_factory=factory,
                )

            self.assertEqual("PASS", result["status"])
            report = (root / "result/values_5/REPORT.md").read_text()
            self.assertIn("CKKS SUM", report)
            self.assertIn("Client normalization divisor", report)
            self.assertIn("Expected normalized SUM", report)
            self.assertIn("Decrypted normalized SUM", report)
            self.assertIn("Restored VND SUM", report)
            self.assertIn("Error (VND)", report)

    def test_benchmark_calls_session_only(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "code/openfhe_direct/benchmarks/synthetic_vnd/ckks_sum.py"
        ).read_text(encoding="utf-8")

        self.assertIn("OpenFHECreditSession", source)
        self.assertNotIn("import openfhe", source)
        self.assertNotIn("EvalSum", source)
        self.assertNotIn("heir-opt", source)


if __name__ == "__main__":
    unittest.main()
